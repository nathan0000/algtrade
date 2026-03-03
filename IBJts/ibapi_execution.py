import requests
import pandas as pd
import xml.etree.ElementTree as ET
import time
import logging
from datetime import datetime

# Set up logging
logging.basicConfig(
    filename='flex_trade_confirmation.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_auth_token():
    """Obtain authentication token from IBKR Client Portal API."""
    # Replace with your actual authentication method
    # Example: Manual token from Client Portal or API authentication
    token = "163148028398705785168513"  # Replace with your token
    logger.info("Using provided authentication token")
    return token

def request_flex_query(token, query_id=1271418, period="1M", flex_version=3):
    """Send request to initiate Flex Query with runtime period parameter."""
    request_base = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
    send_path = "/SendRequest"
    # Include period parameter (e.g., 1M for 1 month)
    send_params = {"t": token, "q": query_id, "v": flex_version, "p": period}
    
    try:
        response = requests.get(url=f"{request_base}{send_path}", params=send_params)
        print(f"Response status code: {response.status_code}")
        response.raise_for_status()
        logger.info(f"Sent Flex Query request: query_id={query_id}, period={period}")
        
        # Parse XML response
        tree = ET.ElementTree(ET.fromstring(response.text))
        root = tree.getroot()
        for child in root:
            if child.tag == "Status" and child.text != "Success":
                logger.error(f"Failed to generate Flex statement: {response.text}")
                raise Exception(f"Failed to generate Flex statement: {child.text}")
            elif child.tag == "ReferenceCode":
                logger.info(f"Received ReferenceCode: {child.text}")
                return child.text
        raise Exception("No ReferenceCode found in response")
    except Exception as e:
        logger.error(f"Error in request_flex_query: {e}")
        raise

def get_flex_statement(token, ref_code, flex_version=3):
    """Retrieve the Flex Query statement using the reference code."""
    request_base = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
    receive_path = "/GetStatement"
    receive_params = {"t": token, "q": ref_code, "v": flex_version}
    
    try:
        response = requests.get(url=f"{request_base}{receive_path}", params=receive_params, allow_redirects=True)
        response.raise_for_status()
        logger.info(f"Retrieved Flex statement for ReferenceCode: {ref_code}")
        return response.content
    except Exception as e:
        logger.error(f"Error in get_flex_statement: {e}")
        raise

def process_trade_confirmation(content, period):
    """Process the Flex Query CSV content into a pandas DataFrame."""
    try:
        # Assume CSV output; adjust if using XML or Text
        df = pd.read_csv(pd.io.common.BytesIO(content))
        # Filter for the specified period (redundant if query is configured correctly)
        df['TradeDate'] = pd.to_datetime(df['TradeDate'])
        if period == "1D":
            start_date = datetime.now() - pd.Timedelta(days=1)
        elif period == "1M":
            start_date = datetime.now() - pd.Timedelta(days=30)
        else:
            start_date = datetime.now() - pd.Timedelta(days=30)  # Fallback
        df = df[df['TradeDate'] >= start_date]
        # Sort by TradeDate and TradeTime
        df.sort_values(by=['TradeDate', 'TradeTime'], inplace=True)
        # Format dates for readability
        df['TradeDate'] = df['TradeDate'].dt.strftime('%Y-%m-%d')
        logger.info(f"Processed {len(df)} trade confirmations for period={period}")
        return df
    except Exception as e:
        logger.error(f"Error processing trade confirmation: {e}")
        raise

def main():
    # Configuration
    query_id = "1271418"  # Replace with your Flex Query ID
    period = "1M"  # Set to '1D' for 1 day, '1M' for 1 month, etc.
    output_file = f"trade_confirmation_report_{period}.csv"
    
    try:
        # Get authentication token
        token = get_auth_token()
        print(f"Using token: {token}")
        # Request Flex Query with period parameter
        logger.info(f"Initiating Flex Query request with period={period}")
        ref_code = request_flex_query(token, query_id, period)
        
        # Wait for statement generation (IBKR recommends 20-30 seconds)
        logger.info("Waiting for statement generation")
        time.sleep(20)
        
        # Retrieve the statement
        content = get_flex_statement(token, ref_code)
        
        # Process and save the report
        df = process_trade_confirmation(content, period)
        df.to_csv(output_file, index=False)
        logger.info(f"Trade confirmation report saved to '{output_file}'")
        print(f"Trade confirmation report saved to '{output_file}'")
        print(f"Total trades in the {period} period: {len(df)}")
        
        # Display sample of the report
        print("\nSample of Trade Confirmation Report:")
        print(df.head())
        
    except Exception as e:
        logger.error(f"Main process failed: {e}")
        print(f"Error: {e}")
        print("Check the Flex Query configuration, token validity, or period parameter. See https://www.interactivebrokers.com/en/trading/flex-queries.php for details.")

if __name__ == "__main__":
    main()