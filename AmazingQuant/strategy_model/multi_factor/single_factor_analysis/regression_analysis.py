# -*- coding: utf-8 -*-

# ------------------------------
# @Time    : 2020/3/31
# @Author  : gao
# @File    : regression_analysis.py
# @Project : AmazingQuant
# ------------------------------

"""
回归法分析
以流通市值平方根或者市值的倒数为权重做WLS,
将因子T期的因子暴露与T+1期股票收益，加权最小二乘法估计因子收益率，得到T-1个数据的因子收益率序列。

(1)单因子回归方程系数T检验值的绝对值均值，通常该值大于2认为是理想的结果，表明因子对收益率的影响显著性程度较高；
(2)单因子回归方程系数T检验值绝对值序列大于2的占比，该值用以解释在测试时间范围内，因子显著性程度的分布特征；
(3)年化因子收益率，该值表明因子对收益率的贡献程度，取年化的原因在于可以与策略年化收益率有个较为直观的比较；
(4)年化波动率,日收益波动率,月收益波动率，该值表明因子对收益率贡献的波动程度，取年化的原因同样在于可以与策略年化收益的波动率有个较为直观的比较；
(5)日收益率分布,月收益率分布,正收益天数,负收益天数,日胜率,月胜率,峰度,偏度
(6)最大回撤
(7)夏普比率,calmar比率,特雷诺比率,索提诺比率
(8)beta,跟踪误差,信息比率,
(9)因子自稳定性系数（FactorStabilityCoeff），该值检验因子收益率的稳定性
"""
#
# m = 1 + 28 + 1，单因子，28行业，流通市值
# m个因子, n只股票
# f  ---- m*1
# x’-----m*n
# W-----n*n
# x----- n*m
# R-----n*1
import datetime

import statsmodels.api as sm
from statsmodels.tsa import stattools
import numpy as np
import pandas as pd

from AmazingQuant.constant import LocalDataFolderName, RightsAdjustment
from AmazingQuant.config.local_data_path import LocalDataPath
from AmazingQuant.data_center.api_data.get_data import get_local_data
from AmazingQuant.data_center.api_data.get_kline import GetKlineData
from AmazingQuant.data_center.api_data.get_index_class import GetIndexClass
from AmazingQuant.data_center.api_data.get_share import GetShare
from AmazingQuant.analysis_center.net_value_analysis import NetValueAnalysis
from apps.server.database_server.database_field.field_multi_factor import FactorRegressionAnalysisResult
from AmazingQuant.constant import DatabaseName
from AmazingQuant.utils.mongo_connection_me import MongoConnect


class RegressionAnalysis(object):
    def __init__(self, factor, factor_name, market_close_data, benchmark_df):
        self.factor = factor
        self.factor_name = factor_name
        self.benchmark_df = benchmark_df

        market_data = market_close_data.reindex(factor.index).reindex(factor.columns, axis=1)
        self.stock_return = market_data.pct_change()

        # 因子收益率，单利，复利, 日收益率，Dataframe, index为时间
        self.factor_return = pd.DataFrame(index=self.factor.index, columns=['cumsum', 'cumprod', 'daily'])
        self.factor_return_daily = None

        # 单因子检测的T值, Series, index为时间
        self.factor_t_value = None
        # 单因子检测的T值的统计值，'t_value_mean': 绝对值均值, 't_value_greater_two':绝对值序列大于2的占比
        self.factor_t_value_statistics = None
        # 净值分析结果
        self.net_analysis_result = {'cumsum': None, 'cumprod': None}
        # 因子收益率的自相关系数acf和偏自相关系数pacf,默认1-10阶,结果list len=11，取1-10个数
        self.acf_result = {'cumsum': {}, 'cumprod': {}}

    def cal_factor_return(self, method='float_value_inverse'):
        """
        method = {‘float_value_inverse’, ‘float_value_square_root’}
        :param method:
        :return:
        """
        index_class_obj = GetIndexClass()
        index_class_obj.get_index_class()
        index_class_obj.get_zero_index_class()

        share_data_obj = GetShare()
        share_data = share_data_obj.get_share('float_a_share_value')

        index_list = self.factor.index
        factor_return_daily = {}
        factor_t_value_dict = {}

        for index in range(self.factor.shape[0]):
            stock_return = self.stock_return.iloc[index].dropna()
            factor_data = self.factor.iloc[index].dropna()

            stock_list = list(set(stock_return.index).intersection(set(factor_data.index)))
            stock_return = stock_return[stock_list].sort_index()
            print(index_list[index])
            index_class_in_date = index_class_obj.get_index_class_in_date(index_list[index]).reindex(
                stock_list).sort_index()

            share_data_in_date = share_data.loc[index_list[index]].reindex(stock_list).dropna()
            share_data_in_date = pd.DataFrame({'float_a_share_value': share_data_in_date[stock_list].sort_index()})
            factor_data = pd.DataFrame({self.factor_name: factor_data[stock_list].sort_index()})

            x = sm.add_constant(pd.concat([index_class_in_date, factor_data, share_data_in_date], axis=1))
            if stock_return.empty:
                factor_return_daily[index_list[index]] = None
                factor_t_value_dict[index_list[index]] = None
                continue
            wls_model = None
            weights = None
            if method == 'float_value_inverse':
                weights = (1. / share_data_in_date['float_a_share_value'])
                weights[np.isinf(weights)] = 0
                wls_model = sm.WLS(stock_return, x, weights=weights)
            elif method == 'float_value_square_root':
                weights = share_data_in_date['float_a_share_value'].values ** 0.5
                wls_model = sm.WLS(stock_return, x, weights=weights)
            weights.name = index_list[index]

            if wls_model is None:
                factor_return_daily[index_list[index]] = None
                factor_t_value_dict[index_list[index]] = None
                continue
            else:
                results = wls_model.fit()
                factor_return_daily[index_list[index]] = results.params[self.factor_name]
                factor_t_value_dict[index_list[index]] = results.tvalues[self.factor_name]

        self.factor_t_value = pd.Series(factor_t_value_dict)
        self.factor_return_daily = pd.Series(factor_return_daily)
        self.factor_return['cumsum'] = self.factor_return_daily.cumsum() + 1
        self.factor_return['cumprod'] = (self.factor_return_daily.add(1)).cumprod()
        self.factor_return['daily'] = self.factor_return_daily

    def cal_t_value_statistics(self):
        t_value_abs = self.factor_t_value.abs()
        t_value_greater_two = t_value_abs[t_value_abs > 2].count() / (t_value_abs.count())
        t_value_mean = self.factor_t_value.mean()
        self.factor_t_value_statistics = pd.Series({'t_value_mean': t_value_mean,
                                                    't_value_greater_two': t_value_greater_two})

    def cal_net_analysis(self):
        start_time, end_time = self.factor_return.dropna().index.min(), self.factor_return.dropna().index.max()
        for i in ['cumsum', 'cumprod']:
            net_value = self.factor_return[i].to_frame('total_balance')
            net_value[net_value < 0] = 0
            net_value_analysis_obj = NetValueAnalysis(net_value, self.benchmark_df, start_time, end_time)
            self.net_analysis_result[i] = net_value_analysis_obj.cal_net_analysis_result()
        return self.net_analysis_result

    def cal_acf(self, nlags=10):
        for i in ['cumsum', 'cumprod']:
            net_value = self.factor_return[i]
            self.acf_result[i]['acf'] = stattools.acf(net_value.dropna().values, fft=False, nlags=nlags)
            self.acf_result[i]['pacf'] = stattools.pacf(net_value.dropna().values, nlags=nlags)
        return self.acf_result

    def save_regression_analysis_result(self, factor_name):
        with MongoConnect(DatabaseName.MULTI_FACTOR_DATA.value):
            factor_return = self.factor_return.copy()
            factor_t_value = self.factor_t_value.copy()
            net_analysis_result = self.net_analysis_result
            factor_return.index = factor_return.index.format()
            factor_t_value.index = factor_t_value.index.format()
            net_analysis_result['cumsum']['net_value_df'].index = net_analysis_result['cumsum'][
                'net_value_df'].index.format()
            net_analysis_result['cumprod']['net_value_df'].index = net_analysis_result['cumprod'][
                'net_value_df'].index.format()
            net_analysis_result['cumsum']['benchmark_df'].index = net_analysis_result['cumsum'][
                'benchmark_df'].index.format()
            net_analysis_result['cumprod']['benchmark_df'].index = net_analysis_result['cumprod'][
                'benchmark_df'].index.format()

            doc = FactorRegressionAnalysisResult(
                factor_name=factor_name,
                # 因子数据开始时间
                begin_date=self.factor.index[0],
                # 因子数据结束时间
                end_date=self.factor.index[-1],
                # 因子收益率的自相关系数acf和偏自相关系数pacf,默认1-10阶,结果list len=11，取1-10个数
                acf_result=self.acf_result,
                # 因子收益率，单利，复利, 日收益率
                factor_return=factor_return,
                # 单因子检测的T值, Series, index为时间
                factor_t_value=factor_t_value,
                # 单因子检测的T值的统计值，'t_value_mean': 绝对值均值, 't_value_greater_two':绝对值序列大于2的占比
                factor_t_value_statistics=self.factor_t_value_statistics,
                # 净值分析结果
                net_analysis_result=self.net_analysis_result
            )
            doc.save()


if __name__ == '__main__':
    factor_name = 'factor_ma5'
    path = LocalDataPath.path + LocalDataFolderName.FACTOR.value + '/'
    factor_ma5 = get_local_data(path, factor_name + '.h5')
    # 指数数据不全，需要删一部分因子数据
    factor_ma5 = factor_ma5[factor_ma5.index < datetime.datetime(2016, 1, 1)]

    kline_object = GetKlineData()
    market_data = kline_object.cache_all_stock_data(dividend_type=RightsAdjustment.BACKWARD.value, field=['close'])
    market_close_data = kline_object.get_market_data(market_data, field=['close'])

    # 指数行情，沪深300代替
    all_index_data = kline_object.cache_all_index_data()
    benchmark_df = kline_object.get_market_data(all_index_data, stock_code=['000300.SH'],
                                                field=['close']).to_frame(name='close')
    # 沪深300 的日线，有脏数据，后续单独处理
    if datetime.datetime(2016, 1, 1) in benchmark_df.index:
        benchmark_df = benchmark_df.drop(datetime.datetime(2016, 1, 1))
    regression_analysis_obj = RegressionAnalysis(factor_ma5, 'factor_name', market_close_data, benchmark_df)
    regression_analysis_obj.cal_factor_return('float_value_inverse')
    regression_analysis_obj.cal_t_value_statistics()
    regression_analysis_obj.cal_net_analysis()
    regression_analysis_obj.cal_acf()

    regression_analysis_obj.save_regression_analysis_result(factor_name)
