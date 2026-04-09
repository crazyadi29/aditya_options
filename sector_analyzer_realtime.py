import requests
import time

class SectorAnalyzerRealtime:
    def __init__(self, cache_time=60):
        self.cache_time = cache_time  # Cache duration in seconds
        self.cache = {}

    def fetch_data(self):
        current_time = time.time()
        if 'data' in self.cache:
            # Check if cached data is still valid
            if current_time - self.cache['timestamp'] < self.cache_time:
                return self.cache['data']

        # Fetch data from NSE APIs
        url = 'https://www.nseindia.com/api/option-chain-indices'
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
        }
        response = requests.get(url, headers=headers)

        if response.status_code == 200:
            self.cache['data'] = response.json()
            self.cache['timestamp'] = current_time
            return self.cache['data']
        else:
            raise Exception('Error fetching data from NSE API')

    def get_nifty_percentage(self):
        data = self.fetch_data()
        # Extract the Nifty percentage from the data
        nifty_data = data['records']['data']
        for index in nifty_data:
            if index['index'] == 'NIFTY':
                return index['lastPrice']

        return 0

if __name__ == '__main__':
    analyzer = SectorAnalyzerRealtime()
    while True:
        nifty_percent = analyzer.get_nifty_percentage()
        print(f'Nifty Percentage: {nifty_percent}%')
        time.sleep(60)  # Wait for 60 seconds before fetching new data
