from fastmcp import FastMCP
import httpx

mcp = FastMCP("PoshBot MCP")

@mcp.tool
async def get_seller_data(seller_id: str):
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
    api = f"https://stage.goshd.com/api/posh_bot/{seller_id}/posh_bot_stats"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(api)
            if response.status_code == 200:
                return response.json()
            return {"msg": "Seller data is not found"}
    except Exception as e:
        return {"msg": str(e)}


@mcp.tool()
def get_temperature(city: str) -> str:
    """
    Gets the temperature for the given city
    Args:
        city: str

    Returns: str

    """
    return f"The temperature in the given city is 25 degrees Celsius"


if __name__ == '__main__':
    mcp.run(transport="sse", port=8001)