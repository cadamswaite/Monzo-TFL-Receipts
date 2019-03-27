import csv,json
import uuid
import requests
import config
import oauth2
import receipt_types
from utils import error
from pprint import pprint
import os
from datetime import datetime, timedelta
csv_dir = "TFL_CSV"

travel_dict = {}


class TFLClient:
    ''' An example single-account client of the Monzo Transaction Receipts API.
        For the underlying OAuth2 implementation, see oauth2.OAuth2Client.
    '''

    def __init__(self):
        self._api_client = oauth2.OAuth2Client()
        self._api_client_ready = False
        self._account_id = None
        self.transactions = []


    def do_auth(self):
        ''' Perform OAuth2 flow mostly on command-line and retrieve information of the
            authorised user's current account information, rather than from joint account,
            if present.
        '''

        print("Starting OAuth2 flow...")
        token = input("If you already have a token, enter it now, otherwise press enter to continue")
        if token == "":
            self._api_client.start_auth()
        else:
            self._api_client.existing_access_token(token)


        print("OAuth2 flow completed, testing API call...")
        response = self._api_client.test_api_call()
        if "authenticated" in response:
            print("API call test successful!")
        else:
            error("OAuth2 flow seems to have failed.")
        self._api_client_ready = True

        print("Retrieving account information...")
        success, response = self._api_client.api_get("accounts", {})
        if not success or "accounts" not in response or len(response["accounts"]) < 1:
            error("Could not retrieve accounts information")

        # We will be operating on personal account only.
        for account in response["accounts"]:
            if "type" in account and account["type"] == "uk_retail":
                self._account_id = account["id"]
                print("Retrieved account information.")
                return

        if self._account_id is None:
            error("Could not find a personal account")


    def match_and_add_receipts(self):
        ''' Find all TFL transactions, match them with the csv ones and upload receipts.'''
        if self._api_client is None or not self._api_client_ready:
            error("API client not initialised.")

        # Our call is not paginated here with e.g. "limit": 10, which will be slow for
        # accounts with a lot of transactions.
        success, response = self._api_client.api_get("transactions?expand[]=merchant", {
            "account_id": self._account_id,
        })

        if not success or "transactions" not in response:
            error("Could not list past transactions ({})".format(response))

        self.transactions = response["transactions"]
        print("All transactions loaded.")
        for trans in self.transactions:
            if trans['merchant'] is not None:
                if trans['merchant']['name']=="Transport for London":
                    if trans['notes'] =="Active card check":
                        continue

                    try: #If transaction was not settled (eg insufficient funds), skip it.
                        date_settled = datetime.strptime(trans['settled'][:10],"%Y-%m-%d")
                    except:
                        continue

                    try: # Might fail if there are custom notes present. Add exception
                        date_travelled = datetime.strptime(trans['notes'][18:],"%A, %d %b")
                    except Exception as e:
                        print("Could not parse note for transaction created %s due to exception %s"%(trans['created'],e))
                        continue

                    # New Year New Edge Case.
                    # Since the note does not include the year, which may be different for transaction and travel.
                    # There is probably a less ugly way to do this..
                    for subtract_days in range(3):
                        possible_date = date_settled.date()-timedelta(days=subtract_days)
                        if possible_date.day == date_travelled.day:
                            date = str(possible_date) # Get in the same format as used by tfl
                            if date in travel_dict:
                                self.add_tfl_receipt(trans,travel_dict[date])
                            break
                    else:
                        print("Could not find any travel during %s"%date_travelled)


    def add_tfl_receipt(self,transaction,list_of_fares):
        # Using a random receipt ID we generate as external ID
        receipt_id = uuid.uuid4().hex
        example_items = []
        for fare in list_of_fares:
            example_items.append(receipt_types.Item(fare[0], 1, "", int(fare[1]), "GBP", 20, []))
        example_payments = []
        example_taxes = []

        example_receipt = receipt_types.Receipt("", receipt_id, transaction["id"],
            abs(transaction["amount"]), "GBP", example_payments, example_taxes, example_items)
        example_receipt_marshaled = example_receipt.marshal()
        #print("Uploading receipt data to API: ", json.dumps(example_receipt_marshaled, indent=4, sort_keys=True))
        #print("")

        success, response = self._api_client.api_put("transaction-receipts/", example_receipt_marshaled)
        if not success:
            error("Failed to upload receipt: {}".format(response))
        print("Successfully uploaded receipt date {}".format(transaction['settled']))
        #print("Successfully uploaded receipt {}: {}\n".format(receipt_id, response))
        return receipt_id


    def process_csv(self, file):
        """Add the entries from the CSV to the travel_dict"""
        with open(csv_dir+"/"+file) as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=',')
            headers = next(csv_reader, None)

            for row in csv_reader:
                date = (row[0])
                datetime_object = datetime.strptime(date, "%d/%m/%Y")
                parsed_date = str(datetime_object.date())
                pence = row[3][1:].replace(".", "")

                if parsed_date in travel_dict:
                    travel_dict[parsed_date].append([row[2],pence])
                else:
                    travel_dict[parsed_date] = [[row[2],pence],]
        #TODO: make this prettier for days with only 1 journey.
        pprint(travel_dict)


    def process_folder(self):
        """Find all the files in the given directory and pass them to process_csv"""
        for root, dirs, files in os.walk(csv_dir):
            for file in files:
                if file.endswith(".csv"):
                    print("\n"*3,file)
                    self.process_csv(file)


if __name__ == "__main__":
    client = TFLClient()
    client.do_auth()
    client.process_folder()
    client.match_and_add_receipts()
    # The webhook endpoint used should be an HTTP-style server served by your own app server.
