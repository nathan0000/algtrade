import math
import scipy.stats as si
import numpy as np
from BS_rate_vix import *

# Black-Scholes Model for Option Pricing and Greeks Calculation
class BlackScholes:
    def __init__(self, S, K, T, r, sigma, option_type='call'):
        """
        S: Underlying stock price
        K: Strike price
        T: Time to expiration in years
        r: Risk-free interest rate (annual)
        sigma: Volatility of the underlying stock (annualized)
        option_type: 'call' or 'put' (default is 'call')
        """
        self.S = S  # Stock price
        self.K = K  # Strike price
        self.T = T  # Time to expiration
        self.r = r  # Risk-free rate
        self.sigma = sigma  # Volatility
        self.option_type = option_type.lower()

        self.d1 = (math.log(self.S / self.K) + (self.r + 0.5 * self.sigma ** 2) * self.T) / (self.sigma * math.sqrt(self.T))
        self.d2 = self.d1 - self.sigma * math.sqrt(self.T)

    def calculate_option_price(self):
        """
        Calculate the option price using the Black-Scholes formula
        """
        if self.option_type == 'call':
            option_price = (self.S * si.norm.cdf(self.d1, 0.0, 1.0) - self.K * math.exp(-self.r * self.T) * si.norm.cdf(self.d2, 0.0, 1.0))
        elif self.option_type == 'put':
            option_price = (self.K * math.exp(-self.r * self.T) * si.norm.cdf(-self.d2, 0.0, 1.0) - self.S * si.norm.cdf(-self.d1, 0.0, 1.0))
        else:
            raise ValueError("Option type must be either 'call' or 'put'.")
        return option_price

    def calculate_delta(self):
        """
        Calculate the Delta of the option
        """
        if self.option_type == 'call':
            delta = si.norm.cdf(self.d1, 0.0, 1.0)
        elif self.option_type == 'put':
            delta = si.norm.cdf(self.d1, 0.0, 1.0) - 1
        return delta

    def calculate_gamma(self):
        """
        Calculate the Gamma of the option
        """
        gamma = si.norm.pdf(self.d1, 0.0, 1.0) / (self.S * self.sigma * math.sqrt(self.T))
        return gamma

    def calculate_vega(self):
        """
        Calculate the Vega of the option
        """
        vega = self.S * si.norm.pdf(self.d1, 0.0, 1.0) * math.sqrt(self.T) / 100
        return vega

    def calculate_theta(self):
        """
        Calculate the Theta of the option
        """
        if self.option_type == 'call':
            theta = (-self.S * si.norm.pdf(self.d1, 0.0, 1.0) * self.sigma / (2 * math.sqrt(self.T)) - self.r * self.K * math.exp(-self.r * self.T) * si.norm.cdf(self.d2, 0.0, 1.0)) / 365
        elif self.option_type == 'put':
            theta = (-self.S * si.norm.pdf(self.d1, 0.0, 1.0) * self.sigma / (2 * math.sqrt(self.T)) + self.r * self.K * math.exp(-self.r * self.T) * si.norm.cdf(-self.d2, 0.0, 1.0)) / 365
        return theta

    def calculate_rho(self):
        """
        Calculate the Rho of the option
        """
        if self.option_type == 'call':
            rho = self.K * self.T * math.exp(-self.r * self.T) * si.norm.cdf(self.d2, 0.0, 1.0) / 100
        elif self.option_type == 'put':
            rho = -self.K * self.T * math.exp(-self.r * self.T) * si.norm.cdf(-self.d2, 0.0, 1.0) / 100
        return rho

# Example Usage:
S = 238.65        # Stock price
K = 240        # Strike price
T = 35 / 365   # Time to expiration (in years)
r = 0.05       # Risk-free rate
sigma = 0.2    # Volatility
option_type = 'put'  # Option type: 'call' or 'put'

r, sigma = GetRateVix("AAPL")
print(f"10-Year Treasury Price: {r}")
print(f"30-Day Historical Volatility: {sigma:.2f}%")

bs = BlackScholes(S, K, T, r, sigma, option_type)

print(f"Option Price: {bs.calculate_option_price():.2f}")
print(f"Delta: {bs.calculate_delta():.2f}")
print(f"Gamma: {bs.calculate_gamma():.2f}")
print(f"Vega: {bs.calculate_vega():.2f}")
print(f"Theta: {bs.calculate_theta():.2f}")
print(f"Rho: {bs.calculate_rho():.2f}")
