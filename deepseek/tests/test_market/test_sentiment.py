# tests/test_market/test_sentiment.py
import pytest
import numpy as np
from market.sentiment import SentimentAnalyzer

class TestSentimentAnalyzer:
    """Test suite for Sentiment Analyzer"""
    
    def test_initialization(self):
        """Test sentiment analyzer initialization"""
        sa = SentimentAnalyzer()
        
        assert sa.put_call_ratio == 0
        assert len(sa.put_call_history) == 0
        assert sa.gex_value == 0
        assert sa.consecutive_up_days == 0
        assert sa.consecutive_down_days == 0
        assert sa.gap_direction == "NONE"
    
    def test_update_put_call_ratio(self):
        """Test updating put/call ratio"""
        sa = SentimentAnalyzer()
        
        sa.update_put_call_ratio(1.25)
        
        assert sa.put_call_ratio == 1.25
        assert len(sa.put_call_history) == 1
        
        # Add more
        sa.update_put_call_ratio(1.15)
        sa.update_put_call_ratio(1.05)
        
        assert len(sa.put_call_history) == 3
    
    def test_update_gamma_exposure(self):
        """Test updating gamma exposure"""
        sa = SentimentAnalyzer()
        
        sa.update_gamma_exposure(1500000)
        
        assert sa.gex_value == 1500000
        
        # Update with trend
        sa.update_gamma_exposure(1600000)
        assert sa.gex_trend == "RISING"
        
        sa.update_gamma_exposure(1550000)
        assert sa.gex_trend == "FALLING"
    
    def test_update_daily_streak_up(self):
        """Test updating daily streak - up days"""
        sa = SentimentAnalyzer()
        
        sa.update_daily_streak(5050, 5000)  # Up day
        assert sa.consecutive_up_days == 1
        assert sa.consecutive_down_days == 0
        
        sa.update_daily_streak(5100, 5050)  # Up day
        assert sa.consecutive_up_days == 2
        
        sa.update_daily_streak(5080, 5100)  # Down day
        assert sa.consecutive_up_days == 0
        assert sa.consecutive_down_days == 1
    
    def test_update_daily_streak_down(self):
        """Test updating daily streak - down days"""
        sa = SentimentAnalyzer()
        
        sa.update_daily_streak(4950, 5000)  # Down day
        assert sa.consecutive_down_days == 1
        assert sa.consecutive_up_days == 0
        
        sa.update_daily_streak(4900, 4950)  # Down day
        assert sa.consecutive_down_days == 2
    
    def test_update_gap_analysis(self):
        """Test gap analysis"""
        sa = SentimentAnalyzer()
        
        # No gap
        sa.update_gap_analysis(5005, 5000)
        assert sa.gap_direction == "NONE"
        assert abs(sa.gap_size) < 0.2
        
        # Gap up
        sa.update_gap_analysis(5050, 5000)
        assert sa.gap_direction == "UP"
        assert sa.gap_size > 0
        
        # Gap down
        sa.update_gap_analysis(4950, 5000)
        assert sa.gap_direction == "DOWN"
        assert sa.gap_size < 0
    
    def test_update_inside_day(self):
        """Test inside day detection"""
        sa = SentimentAnalyzer()
        
        # Inside day
        sa.update_inside_day(5020, 4980, 5050, 4950)
        assert sa.is_inside_day == True
        
        # Not inside day
        sa.update_inside_day(5060, 4940, 5050, 4950)
        assert sa.is_inside_day == False
    
    def test_get_sentiment_signals_normal(self):
        """Test sentiment signals - normal conditions"""
        sa = SentimentAnalyzer()
        
        # Setup normal conditions
        sa.put_call_ratio = 1.0
        sa.put_call_history = [{'ratio': 1.0} for _ in range(10)]
        sa.consecutive_up_days = 1
        sa.consecutive_down_days = 0
        sa.gap_direction = "NONE"
        sa.gex_value = 1000000
        
        signals = sa.get_sentiment_signals()
        
        assert signals['block_put_spreads'] == False
        assert signals['block_call_spreads'] == False
        assert signals['prefer_puts'] == False
        assert signals['prefer_calls'] == False
        assert signals['confidence_multiplier'] == 1.0
    
    def test_get_sentiment_signals_extreme_fear(self):
        """Test sentiment signals - extreme fear"""
        sa = SentimentAnalyzer()
        
        # High put/call ratio (fear)
        sa.put_call_ratio = 1.35
        sa.put_call_history = [{'ratio': 1.3} for _ in range(10)]
        
        signals = sa.get_sentiment_signals()
        
        assert signals['prefer_calls'] == True
        assert signals['confidence_multiplier'] > 1.0
    
    def test_get_sentiment_signals_extreme_greed(self):
        """Test sentiment signals - extreme greed"""
        sa = SentimentAnalyzer()
        
        # Low put/call ratio (greed)
        sa.put_call_ratio = 0.55
        sa.put_call_history = [{'ratio': 0.6} for _ in range(10)]
        
        signals = sa.get_sentiment_signals()
        
        assert signals['prefer_puts'] == True
        assert signals['confidence_multiplier'] > 1.0
    
    def test_get_sentiment_signals_consecutive_up(self):
        """Test sentiment signals - consecutive up days"""
        sa = SentimentAnalyzer()
        sa.consecutive_up_days = 4
        
        signals = sa.get_sentiment_signals()
        
        assert signals['block_call_spreads'] == True
        assert signals['block_put_spreads'] == False
    
    def test_get_sentiment_signals_consecutive_down(self):
        """Test sentiment signals - consecutive down days"""
        sa = SentimentAnalyzer()
        sa.consecutive_down_days = 4
        
        signals = sa.get_sentiment_signals()
        
        assert signals['block_put_spreads'] == True
        assert signals['block_call_spreads'] == False
    
    def test_get_sentiment_signals_gap_up(self):
        """Test sentiment signals - gap up"""
        sa = SentimentAnalyzer()
        sa.gap_direction = "UP"
        sa.gap_size = 0.75
        
        signals = sa.get_sentiment_signals()
        
        assert signals['prefer_puts'] == True
    
    def test_get_sentiment_signals_gap_down(self):
        """Test sentiment signals - gap down"""
        sa = SentimentAnalyzer()
        sa.gap_direction = "DOWN"
        sa.gap_size = -0.75
        
        signals = sa.get_sentiment_signals()
        
        assert signals['prefer_calls'] == True
    
    def test_get_sentiment_signals_inside_day(self):
        """Test sentiment signals - inside day"""
        sa = SentimentAnalyzer()
        sa.is_inside_day = True
        
        signals = sa.get_sentiment_signals()
        
        assert signals['prefer_calls'] == True
    
    def test_get_sentiment_signals_positive_gex(self):
        """Test sentiment signals - positive gamma exposure"""
        sa = SentimentAnalyzer()
        sa.gex_value = 2000000
        
        signals = sa.get_sentiment_signals()
        
        assert signals['prefer_calls'] == True
    
    def test_get_sentiment_signals_negative_gex(self):
        """Test sentiment signals - negative gamma exposure"""
        sa = SentimentAnalyzer()
        sa.gex_value = -2000000
        
        signals = sa.get_sentiment_signals()
        
        assert signals['prefer_puts'] == True