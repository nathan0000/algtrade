# Importing necessary libraries
import numpy as np

def calculate_intrinsic_value(cash_flows, discount_rate):
    intrinsic_value = 0
    for year, cash_flow in enumerate(cash_flows, start=1):
        present_value = cash_flow / (1 + discount_rate) ** year
        intrinsic_value += present_value
    return intrinsic_value

def multi_stage_gordon_valuation(dividends, discount_rate, growth_rates):
    intrinsic_value = 0
    
    for year, dividend in enumerate(dividends, start=1):
        if year < len(growth_rates):
            growth_rate = growth_rates[year - 1]
        else:
            growth_rate = growth_rates[-1]
        
        discounted_dividend = dividend / ((1 + discount_rate) ** year)
        intrinsic_value += discounted_dividend * (1 + growth_rate)
    
    return intrinsic_value

# Define variables
discount_rate = 0.1  # Discount rate (required rate of return)
years = 5            # Number of years for the projection
initial_cash_flow = 59.47e9  # Initial cash flow at year 1
growth_rate = 0.07   # Expected annual growth rate of cash flows

# Calculate projected cash flows for each year
cash_flows = [initial_cash_flow * (1 + growth_rate) ** year for year in range(1, years + 1)]

# Calculate intrinsic value using DCF analysis
intrinsic_value = calculate_intrinsic_value(cash_flows, discount_rate)

# Print the result
print(f"Intrinsic Value: {intrinsic_value:.2f}")

outstanding_shares = 7.43e9  # 7.5 billion shares

# Calculate equity value per share
equity_value_per_share = intrinsic_value / outstanding_shares

# Print the result
print(f"Equity Value Per Share of MSFT: ${equity_value_per_share:.2f}")