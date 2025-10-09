from openai import OpenAI
import json

client = OpenAI(api_key="")
# data = {
#     "available_listings": 1106,
#     "available_listings_total_price": {
#         "val": "50643754.18",
#         "currency_code": "USD",
#         "currency_symbol": "$"
#     },
#     "rolling_self_shares": 205,
#     "moderator_removed_listings": 1,
#     "listings_sold": 2073,
#     "total_earned_amount": {
#         "val": "1389641.37",
#         "currency_code": "USD",
#         "currency_symbol": "$"
#     },
#     "is_top_seller": True,
#     "average_ship_time": 7.7,
#     "is_fast_shipper": False,
#     "is_caution_shipper": True,
#     "average_rating": 4.2,
#     "is_top_rated_seller": False,
#     "total_ratings_count": 65,
#     "seller_5_star_rating_count": 42,
#     "seller_4_star_rating_count": 12,
#     "seller_3_star_rating_count": 3,
#     "seller_2_star_rating_count": 1,
#     "seller_1_star_rating_count": 7,
#     "rating_reasons": {
#         "item_cleanliness": 4,
#         "item_description": 20,
#         "other": 3,
#         "packaging": 3,
#         "seller_responsiveness": 3,
#         "ship_time": 2
#     },
#     "seller_cancelled_orders_delayed": 414,
#     "shares": 956,
#     "is_top_sharer": True,
#     "direct_shares": 1002,
#     "is_top_direct_sharer": True,
#     "new_closets_shared": 105,
#     "is_posh_mentor": True,
#     "posh_recruited": 50,
#     "is_posh_recruiter": True,
#     "sales": 3228
# }
#
# # json_bytes = json.dumps(data).encode("utf-8")
# #
# # file = client.files.create(
# #     file=("seller_data.json", json_bytes),
# #     purpose="user_data"
# # )
# # file_id = file.id
# # print(file_id)



previous_response_id = None
while True:
    query = input('ASK AI : ')
    if query == "":
        break
    response = client.responses.create(
        model="gpt-4.1",
        input=query,
        previous_response_id=previous_response_id,
        # tools=[
        #     {
        #         "type": "code_interpreter",
        #         "container": {
        #             "type": "auto",
        #             "file_ids": ["file-AH1MNvLwGjqpjd9eGhQ7W1"]
        #         }
        #     }
        # ]

        tools=[{
            "type": "mcp",
            "server_label" : "Test",
            "server_description": "Test Server",
            "server_url": "https://quarryable-tenuously-jenniffer.ngrok-free.dev/sse",
            "require_approval": "never",
        }]
    )
    previous_response_id = response.id
    print("AI Response : ", response.output_text)