# verify_imports.py
"""
Script to verify all imports are working correctly
"""

def verify_imports():
    """Test importing all modules"""
    print("Verifying imports...")
    
    modules_to_test = [
        # Config
        ("config", ["AppConfig", "IBKRConfig", "RiskConfig", "StrategyConfig"]),
        
        # Connection
        ("connection.ibkr_client", ["IBKRClient"]),
        ("connection.thread_manager", ["ThreadManager"]),
        
        # Market
        ("market.data_collector", ["MarketDataCollector"]),
        ("market.vix_analyzer", ["VIXAnalyzer"]),
        ("market.first_hour", ["FirstHourAnalyzer", "MarketType"]),
        ("market.sentiment", ["SentimentAnalyzer"]),
        
        # Strategies
        ("strategies.base_strategy", ["BaseStrategy"]),
        ("strategies.put_spread", ["PutCreditSpreadStrategy"]),
        ("strategies.call_spread", ["CallCreditSpreadStrategy"]),
        ("strategies.iron_fly", ["IronFlyStrategy"]),
        ("strategies.iron_condor", ["IronCondorStrategy"]),
        
        # Order Management
        ("order_management.order_manager", ["OrderManager"]),
        ("order_management.risk_manager", ["RiskManager"]),
        
        # Utils
        ("utils.logger", ["setup_logging"]),
        ("utils.helpers", ["round_to_strike", "is_market_hours"]),
    ]
    
    success = True
    
    for module_path, classes in modules_to_test:
        try:
            module = __import__(module_path, fromlist=classes)
            print(f"✅ {module_path}")
            
            # Try to instantiate classes where possible (without required args)
            for class_name in classes:
                if hasattr(module, class_name):
                    print(f"   - Found {class_name}")
                else:
                    print(f"   ❌ Missing {class_name}")
                    success = False
                    
        except ImportError as e:
            print(f"❌ {module_path}: {e}")
            success = False
    
    # Test main application
    try:
        from main import SPX0DTEAutoTrader
        print("✅ main.SPX0DTEAutoTrader")
    except ImportError as e:
        print(f"❌ main: {e}")
        success = False
    
    return success

if __name__ == "__main__":
    import sys
    import os
    
    # Add project root to path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    
    success = verify_imports()
    
    if success:
        print("\n✅ All imports verified successfully!")
        sys.exit(0)
    else:
        print("\n❌ Some imports failed!")
        sys.exit(1)