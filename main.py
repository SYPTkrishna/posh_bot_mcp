from exceptiongroup import catch
from fastmcp import FastMCP
import requests

mcp = FastMCP("PoshBot MCP")

@mcp.tool()
def get_seller_data(seller_id: str):
    """
    Retrieves seller statistics and bot data from an external API.

    This function fetches comprehensive seller information including their
    performance metrics, bot statistics, and activity data from the posh_bot_stats
    endpoint.

    Args:
        seller_id (str): The unique identifier of the seller whose data should be retrieved.
                        This ID is used to construct the API endpoint URL.

    Returns:
        dict: On success, returns the API response containing seller statistics.
              On failure, returns a dictionary with an error message:
              {"msg": "Seller data is not found"}
    """
    api = "https://stage.goshd.com/api/posh_bot/"+str(seller_id)+"/posh_bot_stats"
    try:
        response = requests.get(api)
        return response
    except Exception as e:
        return { "msg" : "Seller data is not found"}


if __name__ == '__main__':
    mcp.run()