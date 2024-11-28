from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order

class MyWrapper(EWrapper):
    def __init__(self, client):
        self.client = client
        self.spy_price = None

    def nextValidId(self, orderId: int):
        self.nextOrderId = orderId
#        self.place_initial_orders()
        self.request_market_data()

    def request_market_data(self):
        # Define the contract for SPY
        contract = Contract()
        contract.symbol = "SPY"
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"

        # Request real-time market data for SPY
        self.client.reqMarketDataType(1) # Set market data type to delayed
        self.client.reqMktData(self.nextOrderId, contract, "", False, False, [])

    def tickPrice(self, reqId, tickType, price, attrib):
        if tickType == 4: # 4 correspnds to last trade price
            self.spy_price = price
            print(f"Received SPY price update: {self.spy_price}")

    def place_initial_orders(self):
        # Define the contract for SPY options
        contract = Contract()
        contract.symbol = "SPY"
        contract.secType = "OPT"
        contract.exchange = "SMART"
        contract.currency = "USD"
        
        # Define the at-the-money and out-of-the-money strike prices
        atm_strike = self.spy_price  # Replace with the actual ATM strike price
        otm_strike = 0.007 * atm_strike  # 0.7% out-of-the-money
        
        # Create orders for shorting ATM call and put options
        short_call_order = self.create_option_order(contract, atm_strike, "CALL", "SELL")
        short_put_order = self.create_option_order(contract, atm_strike, "PUT", "SELL")
        
        # Create orders for buying back OTM call and put options
        buy_back_call_order = self.create_option_order(contract, otm_strike, "CALL", "BUY")
        buy_back_put_order = self.create_option_order(contract, otm_strike, "PUT", "BUY")
        
        # Attach order conditions to roll over long call or put option
        roll_over_call_order = self.create_option_order(contract, atm_strike, "CALL", "BUY")
        roll_over_put_order = self.create_option_order(contract, atm_strike, "PUT", "BUY")
        roll_over_call_order.conditions = [self.create_condition("Price", "SPY", "ASK", "<=", otm_strike)]
        roll_over_put_order.conditions = [self.create_condition("Price", "SPY", "BID", ">=", otm_strike)]
        
        # Place orders
        self.place_order(short_call_order)
        self.place_order(short_put_order)
        self.place_order(buy_back_call_order)
        self.place_order(buy_back_put_order)
        self.place_order(roll_over_call_order)
        self.place_order(roll_over_put_order)

    def create_option_order(self, contract, strike, right, action):
        order = Order()
        order.action = action
        order.orderType = "LMT"
        order.totalQuantity = 1
        order.lmtPrice = 0.01  # Replace with your desired limit price
        order.transmit = False

        contract.strike = strike
        contract.right = right
        order.orderRef = f"{action}_{right}_{strike}"
        order.orderId = self.nextOrderId
        self.nextOrderId += 1
        return order

    def create_condition(self, type, conId, exchange, condition, value):
        condition = {
            "type": type,
            "conId": conId,
            "exchange": exchange,
            "condition": condition,
            "value": value
        }
        return condition

    def place_order(self, order):
        self.client.placeOrder(order.orderId, contract, order)

class MyClient(EClient, MyWrapper):
    def __init__(self):
        EClient.__init__(self, self)
        MyWrapper.__init__(self, self)

if __name__ == "__main__":
    client = MyClient()
    client.connect("127.0.0.1", 7497, clientId=0)  # Connect to TWS on the default port
    client.run()
