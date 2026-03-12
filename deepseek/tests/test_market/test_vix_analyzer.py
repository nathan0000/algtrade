# tests/test_market/test_vix_analyzer.py
import pytest
import numpy as np
from market.vix_analyzer import VIXAnalyzer

class TestVIXAnalyzer:
    """Test suite for VIX Analyzer"""
    
    def test_initialization(self, mock_ibkr_client):
        """Test VIX analyzer initialization"""
        va = VIXAnalyzer(mock_ibkr_client)
        
        assert va.ibkr == mock_ibkr_client
        assert va.current_vix == 0
        assert len(va.vix_history) == 0
        assert va.vix_ma20 == 0
        assert va.vix_ma50 == 0
    
    def test_update_vix(self, mock_ibkr_client):
        """Test updating VIX value"""
        va = VIXAnalyzer(mock_ibkr_client)
        
        va.update_vix(16.5)
        
        assert va.current_vix == 16.5
        assert len(va.vix_history) == 1
        assert va.vix_history[0]['value'] == 16.5
    
    def test_update_vix_multiple(self, mock_ibkr_client):
        """Test multiple VIX updates"""
        va = VIXAnalyzer(mock_ibkr_client)
        
        for vix in [16.5, 17.2, 16.8, 16.3, 15.9]:
            va.update_vix(vix)
        
        assert len(va.vix_history) == 5
        assert va.current_vix == 15.9
    
    def test_calculate_indicators(self, mock_ibkr_client):
        """Test VIX indicator calculation"""
        va = VIXAnalyzer(mock_ibkr_client)
        
        # Add enough history for indicators
        for vix in np.random.normal(17, 2, 60):
            va.update_vix(vix)
        
        va.calculate_indicators()
        
        assert va.vix_ma20 > 0
        assert va.vix_ma50 > 0
        assert 0 <= va.vix_percentile <= 100
    
    def test_get_regime_low(self, mock_ibkr_client):
        """Test VIX regime classification - LOW"""
        va = VIXAnalyzer(mock_ibkr_client)
        va.current_vix = 14.5
        va.vix_ma20 = 15.0
        
        regime = va.get_regime()
        
        assert regime['regime'] == 'LOW'
        assert 'range-bound' in regime['implication'].lower()
        assert 'Iron Condor' in regime['preferred_strategies'][0]
        assert regime['block_long'] == False
        assert regime['block_short'] == False
    
    def test_get_regime_normal(self, mock_ibkr_client):
        """Test VIX regime classification - NORMAL"""
        va = VIXAnalyzer(mock_ibkr_client)
        va.current_vix = 17.5
        va.vix_ma20 = 18.0
        
        regime = va.get_regime()
        
        assert regime['regime'] == 'NORMAL'
        assert 'Balanced' in regime['implication']
        assert 'All strategies' in regime['preferred_strategies'][0]
    
    def test_get_regime_elevated(self, mock_ibkr_client):
        """Test VIX regime classification - ELEVATED"""
        va = VIXAnalyzer(mock_ibkr_client)
        va.current_vix = 22.5
        va.vix_ma20 = 21.0
        
        regime = va.get_regime()
        
        assert regime['regime'] == 'ELEVATED'
        assert 'Caution' in regime['implication']
        assert 'Directional spreads' in regime['preferred_strategies'][0]
    
    def test_get_regime_high(self, mock_ibkr_client):
        """Test VIX regime classification - HIGH"""
        va = VIXAnalyzer(mock_ibkr_client)
        va.current_vix = 28.5
        va.vix_ma20 = 25.0
        
        regime = va.get_regime()
        
        assert regime['regime'] == 'HIGH'
        assert 'Avoid premium selling' in regime['preferred_strategies'][0]
        assert regime['block_long'] == True
        assert regime['block_short'] == True
    
    def test_get_regime_trend_rising(self, mock_ibkr_client):
        """Test VIX trend detection - RISING"""
        va = VIXAnalyzer(mock_ibkr_client)
        va.current_vix = 18.5
        va.vix_ma20 = 17.0
        
        regime = va.get_regime()
        
        assert regime['trend'] == 'RISING'
    
    def test_get_regime_trend_falling(self, mock_ibkr_client):
        """Test VIX trend detection - FALLING"""
        va = VIXAnalyzer(mock_ibkr_client)
        va.current_vix = 16.5
        va.vix_ma20 = 17.0
        
        regime = va.get_regime()
        
        assert regime['trend'] == 'FALLING'
    
    def test_should_trade_normal(self, mock_ibkr_client):
        """Test should_trade with normal VIX"""
        va = VIXAnalyzer(mock_ibkr_client)
        va.current_vix = 18.5
        
        assert va.should_trade() == True
    
    def test_should_trade_high(self, mock_ibkr_client):
        """Test should_trade with high VIX"""
        va = VIXAnalyzer(mock_ibkr_client)
        va.current_vix = 32.5
        
        assert va.should_trade() == False