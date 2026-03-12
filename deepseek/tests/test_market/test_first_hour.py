# tests/test_market/test_first_hour.py
import pytest
from datetime import datetime, time
import pytz
from market.first_hour import FirstHourAnalyzer, MarketType

class TestFirstHourAnalyzer:
    """Test suite for First Hour Analyzer"""
    
    def test_initialization(self):
        """Test first hour analyzer initialization"""
        fh = FirstHourAnalyzer()
        
        assert len(fh.first_hour_data) == 0
        assert fh.pre_market_high == 0
        assert fh.pre_market_low == float('inf')
        assert fh.market_type == MarketType.UNCLEAR
    
    def test_set_reference_levels(self):
        """Test setting reference levels"""
        fh = FirstHourAnalyzer()
        
        fh.set_reference_levels(
            pre_high=5020.0,
            pre_low=4980.0,
            y_close=5000.0,
            y_high=5050.0,
            y_low=4950.0
        )
        
        assert fh.pre_market_high == 5020.0
        assert fh.pre_market_low == 4980.0
        assert fh.yesterday_close == 5000.0
        assert fh.yesterday_high == 5050.0
        assert fh.yesterday_low == 4950.0
    
    def test_add_first_hour_bar(self, ny_tz):
        """Test adding first hour bars"""
        fh = FirstHourAnalyzer()
        
        bar_time = datetime(2025, 3, 8, 9, 35, tzinfo=ny_tz)
        fh.add_first_hour_bar(bar_time, 5000, 5010, 4995, 5005, 10000)
        
        assert len(fh.first_hour_data) == 1
        assert fh.first_hour_data[0]['open'] == 5000
        assert fh.first_hour_data[0]['high'] == 5010
        assert fh.first_hour_data[0]['low'] == 4995
        assert fh.first_hour_data[0]['close'] == 5005
        assert fh.first_hour_data[0]['volume'] == 10000
    
    def test_analyze_insufficient_data(self):
        """Test analysis with insufficient data"""
        fh = FirstHourAnalyzer()
        
        # Add only 3 bars
        for i in range(3):
            fh.add_first_hour_bar(datetime.now(), 5000, 5010, 4990, 5005, 10000)
        
        result = fh.analyze()
        
        assert result['market_type'] == MarketType.UNCLEAR
        assert result['confidence'] == 0
    
    def test_analyze_trending_bullish(self, ny_tz):
        """Test analysis for bullish trend"""
        fh = FirstHourAnalyzer()
        fh.set_reference_levels(5010, 4990, 5000, 5020, 4980)
        
        # Add bullish bars (higher highs, higher lows)
        bars = [
            (9, 35, 5000, 5010, 4995, 5005, 10000),
            (9, 40, 5005, 5015, 5000, 5012, 12000),
            (9, 45, 5012, 5025, 5010, 5022, 15000),
            (9, 50, 5022, 5030, 5020, 5028, 18000),
            (9, 55, 5028, 5040, 5025, 5035, 20000),
            (10, 0, 5035, 5050, 5030, 5045, 22000)
        ]
        
        for hour, minute, open_, high, low, close, volume in bars:
            bar_time = datetime(2025, 3, 8, hour, minute, tzinfo=ny_tz)
            fh.add_first_hour_bar(bar_time, open_, high, low, close, volume)
        
        result = fh.analyze()
        
        assert result['market_type'] == MarketType.TRENDING_BULLISH
        assert result['confidence'] > 50
        assert result['broke_pre_high'] == True
    
    def test_analyze_trending_bearish(self, ny_tz):
        """Test analysis for bearish trend"""
        fh = FirstHourAnalyzer()
        fh.set_reference_levels(5010, 4990, 5000, 5020, 4980)
        
        # Add bearish bars (lower highs, lower lows)
        bars = [
            (9, 35, 5000, 5005, 4990, 4995, 10000),
            (9, 40, 4995, 5000, 4985, 4988, 12000),
            (9, 45, 4988, 4995, 4975, 4980, 15000),
            (9, 50, 4980, 4985, 4965, 4970, 18000),
            (9, 55, 4970, 4975, 4955, 4960, 20000),
            (10, 0, 4960, 4965, 4945, 4950, 22000)
        ]
        
        for hour, minute, open_, high, low, close, volume in bars:
            bar_time = datetime(2025, 3, 8, hour, minute, tzinfo=ny_tz)
            fh.add_first_hour_bar(bar_time, open_, high, low, close, volume)
        
        result = fh.analyze()
        
        assert result['market_type'] == MarketType.TRENDING_BEARISH
        assert result['confidence'] > 50
        assert result['broke_pre_low'] == True
    
    def test_analyze_range_bound(self, ny_tz):
        """Test analysis for range-bound market"""
        fh = FirstHourAnalyzer()
        fh.set_reference_levels(5020, 4980, 5000, 5050, 4950)
        
        # Add range-bound bars (oscillating within range)
        bars = [
            (9, 35, 5000, 5010, 4995, 5005, 10000),
            (9, 40, 5005, 5015, 5000, 5008, 12000),
            (9, 45, 5008, 5012, 4998, 5002, 15000),
            (9, 50, 5002, 5018, 5001, 5015, 18000),
            (9, 55, 5015, 5020, 5005, 5010, 20000),
            (10, 0, 5010, 5015, 5002, 5005, 22000)
        ]
        
        for hour, minute, open_, high, low, close, volume in bars:
            bar_time = datetime(2025, 3, 8, hour, minute, tzinfo=ny_tz)
            fh.add_first_hour_bar(bar_time, open_, high, low, close, volume)
        
        result = fh.analyze()
        
        assert result['market_type'] == MarketType.RANGE_BOUND
        assert result['confidence'] > 50
        assert result['range_score'] > 50
    
    def test_get_recommended_strategies(self):
        """Test strategy recommendations based on market type"""
        fh = FirstHourAnalyzer()
        
        # Test bullish
        fh.market_type = MarketType.TRENDING_BULLISH
        strategies = fh.get_recommended_strategies()
        assert "Put Credit Spread" in strategies
        
        # Test bearish
        fh.market_type = MarketType.TRENDING_BEARISH
        strategies = fh.get_recommended_strategies()
        assert "Call Credit Spread" in strategies
        
        # Test range-bound
        fh.market_type = MarketType.RANGE_BOUND
        strategies = fh.get_recommended_strategies()
        assert "Iron Condor" in strategies
        assert "Iron Fly" in strategies
        
        # Test unclear
        fh.market_type = MarketType.UNCLEAR
        strategies = fh.get_recommended_strategies()
        assert "Iron Condor" in strategies