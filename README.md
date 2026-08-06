# Cargoboard MCP Server

A Model Context Protocol (MCP) server for the Cargoboard shipping API. This server provides essential functionality for creating quotations, orders, and generating shipment labels through a streamlined interface.

## Features

The MCP Server provides 7 essential tools:

- **list_orders** - List orders and discover their API CUID/reference
- **list_quotations** - List quotations and discover their API ID
- **create_quotation** - Create shipping quotation requests
- **create_order** - Create shipping orders  
- **get_quotation** - Retrieve quotation details
- **get_order** - Retrieve order details
- **print_shipment_labels** - Generate shipment labels for orders

## Setup

### Prerequisites

- Python 3.10 or higher
- `uv` package manager
- Cargoboard API key

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd cargo-mcp
   ```

2. **Install dependencies**
   ```bash
   uv sync
   ```

3. **Configure environment variables**

   Copy `.env.example` to `.env` for local development and replace all
   placeholders. Never commit the resulting `.env` file.

   ```bash
   CARGOBOARD_API_KEY=replace-with-cargoboard-api-key
   CARGOBOARD_BASE_URL=https://api.cargoboard.com
   CARGOBOARD_TIMEOUT=30
   PORT=8000
   ENTRA_CLIENT_ID=00000000-0000-0000-0000-000000000000
   ENTRA_CLIENT_SECRET=replace-with-entra-client-secret-value
   ENTRA_TENANT_ID=00000000-0000-0000-0000-000000000000
   ENTRA_SCOPE=mcp.access
   MCP_PUBLIC_BASE_URL=http://localhost:8000
   OAUTH_JWT_SIGNING_KEY=replace-with-at-least-32-random-characters
   OAUTH_STORAGE_BACKEND=memory
   CARGOBOARD_ENABLE_WRITE_TOOLS=false
   ```

   `OAUTH_STORAGE_BACKEND=memory` is accepted only for a loopback URL. Railway
   deployments must use Redis-backed encrypted OAuth storage as documented in
   `.env.example`.

### Microsoft Entra app registration

Create a **single-tenant** app registration for the pilot:

1. Add a Web redirect URI matching the MCP server origin plus
   `/auth/callback`, for example `http://localhost:8000/auth/callback`.
2. Under **Expose an API**, keep `api://<ENTRA_CLIENT_ID>` as the Application
   ID URI and add the delegated scope `mcp.access`.
3. In the manifest, set `api.requestedAccessTokenVersion` to `2`.
4. Create a client secret and store only its **Value** in
   `ENTRA_CLIENT_SECRET`.
5. For production, set **Assignment required?** on the Enterprise Application
   and assign only the approved users or group.

The app registration callback is the MCP server's `/auth/callback`. Claude's
hosted callback `https://claude.ai/api/mcp/auth_callback` is registered
dynamically with the FastMCP OAuth proxy and is allowlisted by the server.

### HTTP endpoint

Start the Streamable HTTP server with:

```bash
uv run python server.py
```

The endpoints are:

- `GET /health` returns only `ok` and does not require the secret.
- `/mcp` is the OAuth-protected Streamable HTTP MCP endpoint.
- OAuth discovery, registration, consent, token, and callback routes are
  exposed by FastMCP as required by the MCP authorization specification.

The server binds to `0.0.0.0` and reads its port from `PORT`, which Railway
provides at runtime.

### Claude.ai custom connector

In organization settings, open **Connectors**, add a custom **Web** connector,
and enter the Railway MCP URL:

```text
https://<railway-domain>/mcp
```

Leave Claude's optional OAuth Client ID and Client Secret fields empty. FastMCP
supports Dynamic Client Registration and forwards the interactive login to the
configured Microsoft Entra tenant. Every user must complete the Microsoft login
and consent flow; requests without a valid access token receive `401`.

### Local OAuth smoke test

With the server running and the localhost callback registered in Entra, start
the interactive test client in another terminal:

```bash
uv run python scripts/test_oauth.py
```

The browser opens the Microsoft login and consent flow. After a successful
login, the script prints only the available tool names; it never prints access
tokens or credentials. `create_order` must not appear while
`CARGOBOARD_ENABLE_WRITE_TOOLS=false`.

### Write-tool safety

`create_order` is hidden by default. It is exposed only when
`CARGOBOARD_ENABLE_WRITE_TOOLS=true`. Quotations remain available because they
are explicitly allowed for the pilot. Do not enable order creation without a
separate operational approval.

### Discovering order and quotation IDs

Use `list_orders` before `get_order` when only a display number is known. The
Cargoboard detail endpoint accepts the API CUID in `id` or the order
`reference`; values such as `sequence` or an ERP order number are not
interchangeable with those identifiers. `list_quotations` provides the same
discovery step for quotation IDs.

Both list tools are read-only and return at most 50 records per call. Use the
returned cursor to request subsequent pages. Optional `order_by` and `filters`
values are forwarded as Cargoboard query expressions after applying count,
length, and control-character limits.

### Legacy stdio client setup

The hosted server uses HTTP. To run the same tool implementation locally over
stdio, use FastMCP's CLI instead of `python server.py`:

```json
{
  "mcpServers": {
    "cargo-mcp": {
      "command": "uv",
      "args": [
        "run",
        "fastmcp",
        "run",
        "/absolute/path/to/server.py:app"
      ]
    }
  }
}
```

Replace `/absolute/path/to/server.py` with the actual path and provide the
Cargoboard environment variables through a local `.env` file.

## Usage Guide

### Creating a Quotation

Use the `create_quotation` tool to get shipping price estimates:

```python
# Basic quotation request
quotation_data = {
    "product": "STANDARD",
    "shipper": {
        "reference": "SHIPPER-001",
        "name": "My Company",
        "address": {
            "postCode": "10115", 
            "countryCode": "DE"
        },
        "contactPerson": {
            "name": "John Doe",
            "phone": "+49123456789",
            "email": "john@company.com"
        }
    },
    "consignee": {
        "reference": "CUSTOMER-001",
        "name": "Customer Company", 
        "address": {
            "postCode": "20095",
            "countryCode": "DE"
        },
        "contactPerson": {
            "name": "Jane Smith",
            "phone": "+49987654321", 
            "email": "jane@customer.com"
        }
    },
    "lines": [{
        "content": "Electronics",
        "unitQuantity": 2,
        "unitPackageType": "PA",  # Package type
        "unitLength": 100,        # cm
        "unitWidth": 80,          # cm
        "unitHeight": 60,         # cm
        "unitWeight": 15.5        # kg
    }]
}
```

### Creating an Order

Use the `create_order` tool to place actual shipping orders:

```python
# Complete order request
order_data = {
    "product": "STANDARD",
    "shipper": {
        "reference": "SHIPPER-001",
        "name": "My Company",
        "address": {
            "postCode": "10115",
            "countryCode": "DE", 
            "street": "Main Street 123",        # Required for orders
            "city": "Berlin"                    # Required for orders
        },
        "contactPerson": {
            "name": "John Doe",
            "phone": "+49123456789",
            "email": "john@company.com"
        }
    },
    "consignee": {
        "reference": "CUSTOMER-001", 
        "name": "Customer Company",
        "address": {
            "postCode": "20095",
            "countryCode": "DE",
            "street": "Customer Alley 456",     # Required for orders
            "city": "Hamburg"                   # Required for orders
        },
        "contactPerson": {
            "name": "Jane Smith",
            "phone": "+49987654321",
            "email": "jane@customer.com"
        }
    },
    "lines": [{
        "content": "Electronics",
        "unitQuantity": 2,
        "unitPackageType": "PA",
        "unitLength": 100,
        "unitWidth": 80,
        "unitHeight": 60,
        "unitWeight": 15.5
    }],
    "wants_climate_neutral_shipment": True,    # Optional: eco-friendly shipping
    "wants_insurance": False,                  # Optional: shipment insurance
    "customer_order_code": "CUST-ORD-123"     # Optional: your internal reference
}
```

### Retrieving Information

**Get quotation details:**
```python
# Retrieve quotation by ID
quotation = get_quotation("quotation_id_here")
```

**Get order details:**
```python
# Retrieve order by ID  
order = get_order("order_id_here")
```

### Generating Shipment Labels

**Print shipping labels:**
```python
# Generate labels for confirmed order
labels = print_shipment_labels("order_id_here")
```

## Product Types

Available shipping products:

- **STANDARD** - Standard delivery service
- **EXPRESS** - Express delivery service  
- **EXPRESS_8** - Express delivery by 8 AM
- **EXPRESS_10** - Express delivery by 10 AM
- **EXPRESS_12** - Express delivery by 12 PM
- **EXPRESS_16** - Express delivery by 4 PM
- **FIX** - Fixed time delivery
- **FIX_8** - Fixed time delivery by 8 AM
- **FIX_10** - Fixed time delivery by 10 AM
- **FIX_12** - Fixed time delivery by 12 PM
- **FIX_16** - Fixed time delivery by 4 PM
- **DIRECT** - Direct delivery service

## Package Types

Common package type codes:

- **KT** - Package/Parcel
- **FP** - Euro Pallet
- **EP** - One Way Pallet

## Address Requirements

### For Quotations:
- `postCode` (required)
- `countryCode` (required, ISO 2-letter code)
- `name` (optional but recommended)

### For Orders:
- `postCode` (required)
- `countryCode` (required)
- `street` (required)
- `city` (required)
- `name` (required)

## Development

### Running the Server

```bash
# Run MCP server directly
uv run python server.py

# Run with specific environment
CARGOBOARD_API_KEY=your_key uv run python server.py
```

### Linting

```bash
# Run ruff linter
uv run ruff check .

# Auto-fix issues
uv run ruff check --fix .
```

### Testing

Test your setup:

```bash
# Run the server directly to test startup
timeout 5 uv run python server.py
```

## Error Handling

The server includes comprehensive error handling and logging. Common issues:

1. **Authentication Error**: Check your API key in the `.env` file
2. **Validation Error**: Ensure all required fields are provided with correct formats
3. **Network Error**: Check your internet connection and API endpoint URL
4. **Order Not Found**: Verify the order/quotation ID exists and is accessible

## Support

For API documentation and support:
- API Documentation: [Cargoboard API Docs](https://docs.cargoboard.com/)
- Contact support via Email: mcp@cargoboard.com
