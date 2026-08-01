#!/usr/bin/env python3
"""
Cargoboard Customer MCP Server

A simplified Model Context Protocol (MCP) server for Cargoboard API customers
that provides essential tools for quotations, orders, and shipment labels.

Example shipper/consignee structure for orders:
{
    "reference": "REF123",
    "name": "Company Name",
    "address": {
        "postCode": "12345",
        "countryCode": "DE",
        "street": "Example Street 1",
        "city": "Example City"
    },
    "contactPerson": {
        "name": "John Doe",
        "phone": "+49123456789",
        "email": "john@example.com"
    }
}

Example shipper/consignee structure for quotations (less address details required):
{
    "reference": "REF123",
    "name": "Company Name",
    "address": {
        "postCode": "12345",
        "countryCode": "DE"
    },
    "contactPerson": {
        "name": "John Doe",
        "phone": "+49123456789",
        "email": "john@example.com"
    }
}

Example line structure:
{
    "content": "Test goods",
    "unitQuantity": 1,
    "unitPackageType": "PA",
    "unitLength": 100,
    "unitWidth": 80,
    "unitHeight": 60,
    "unitWeight": 10.5
}
"""

import os
import logging
import re
from typing import Optional, Dict, Any
from dataclasses import dataclass

import httpx
from fastmcp import FastMCP
from mcp.types import Icon
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from auth_config import build_entra_provider

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

_RESOURCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse an opt-in boolean environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _validated_resource_id(value: str) -> str:
    """Restrict identifiers before interpolating them into upstream URL paths."""
    if not _RESOURCE_ID_PATTERN.fullmatch(value):
        raise ValueError("Invalid identifier")
    return value


def _port_from_env() -> int:
    """Read Railway's dynamic port with a safe local default."""
    try:
        port = int(os.getenv("PORT", "8000"))
    except ValueError as exc:
        raise RuntimeError("PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT must be between 1 and 65535")
    return port


class Address(BaseModel):
    """Address information for shipper/consignee"""
    model_config = ConfigDict(populate_by_name=True)

    post_code: str = Field(alias="postCode")
    country_code: str = Field(alias="countryCode")
    street: Optional[str] = None
    city: Optional[str] = None

class ContactPerson(BaseModel):
    """Contact person information"""
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None


class ShipperConsignee(BaseModel):
    """Shipper or consignee information"""
    model_config = ConfigDict(populate_by_name=True)

    reference: str
    name: Optional[str] = None  # Required for orders, optional for quotations
    address: Address
    contact_person: Optional[ContactPerson] = Field(alias="contactPerson", default=None)

class LineItem(BaseModel):
    """Line item for shipment"""
    model_config = ConfigDict(populate_by_name=True)

    content: str
    unit_quantity: int = Field(alias="unitQuantity")
    unit_package_type: str = Field(alias="unitPackageType")
    unit_length: float = Field(alias="unitLength")
    unit_width: float = Field(alias="unitWidth")
    unit_height: float = Field(alias="unitHeight")
    unit_weight: float = Field(alias="unitWeight")

@dataclass
class CargoboardConfig:
    """Configuration for Cargoboard API connection"""

    base_url: str = "https://api.cargoboard.com"
    api_key: Optional[str] = None
    timeout: int = 30

    @classmethod
    def from_env(cls) -> "CargoboardConfig":
        """Create configuration from environment variables"""
        return cls(
            base_url=os.getenv("CARGOBOARD_BASE_URL", "https://api.cargoboard.com"),
            api_key=os.getenv("CARGOBOARD_API_KEY"),
            timeout=int(os.getenv("CARGOBOARD_TIMEOUT", "30")),
        )

    def get_headers(self) -> Dict[str, str]:
        """Get authentication headers"""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        if self.api_key:
            headers["X-API-KEY"] = self.api_key

        return headers

    def validate(self) -> None:
        """Fail before serving traffic when production configuration is unsafe."""
        if not self.api_key:
            raise RuntimeError("CARGOBOARD_API_KEY is required")
        if not self.base_url.startswith("https://"):
            raise RuntimeError("CARGOBOARD_BASE_URL must use HTTPS")
        if self.timeout <= 0:
            raise RuntimeError("CARGOBOARD_TIMEOUT must be greater than zero")


class CargoboardClient:
    """HTTP client for Cargoboard API"""

    def __init__(self, config: CargoboardConfig):
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=config.base_url,
            headers=config.get_headers(),
            timeout=config.timeout,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def get(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make GET request to Cargoboard API"""
        try:
            response = await self.client.get(endpoint, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Cargoboard GET failed with status %s",
                exc.response.status_code,
            )
            raise RuntimeError("Cargoboard API request failed") from None
        except (httpx.RequestError, ValueError):
            logger.error("Cargoboard GET failed")
            raise RuntimeError("Cargoboard API request failed") from None

    async def post(
        self, endpoint: str, json_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Make POST request to Cargoboard API"""
        try:
            response = await self.client.post(endpoint, json=json_data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Cargoboard POST failed with status %s",
                exc.response.status_code,
            )
            raise RuntimeError("Cargoboard API request failed") from None
        except (httpx.RequestError, ValueError):
            logger.error("Cargoboard POST failed")
            raise RuntimeError("Cargoboard API request failed") from None


# Initialize FastMCP server
def _add_optional_fields(data: dict, **kwargs) -> dict:
    """Add optional fields to data dict if provided"""
    field_mapping = {
        'customer_order_code': 'customerOrderCode',
        'coupon_code': 'couponCode',
        'uit_code': 'uitCode',
        'customs_tariff_quantity': 'customsTariffQuantity',
        'incoterm': 'incoterm',
        'is_supplying_company_or_receiving_customer': 'isSupplyingCompanyOrReceivingCustomer',
        'value_of_goods_amount': 'valueOfGoodsAmount',
        'value_of_goods_currency': 'valueOfGoodsCurrency',
        'tax_declaration': 'taxDeclaration',
        'lines_pallet_bays': 'linesPalletBays'
    }
    
    for param_name, api_field in field_mapping.items():
        value = kwargs.get(param_name)
        if value is not None:
            data[api_field] = value
    return data


app = FastMCP(
    "Naturbummler Cargoboard",
    website_url="https://naturbummler.de",
    icons=[
        Icon(
            src=(
                "https://naturbummler.de/cdn/shop/files/"
                "1000x628px_Logo_1000x628.png"
            ),
            mimeType="image/png",
            sizes=["1000x628"],
        )
    ],
    auth=build_entra_provider(),
    mask_error_details=True,
    tasks=False,
)
config = CargoboardConfig.from_env()

logger.info("Cargoboard MCP server configured")


@app.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health_check(request: Request) -> PlainTextResponse:
    """Return a minimal unauthenticated liveness response for Railway."""
    return PlainTextResponse("ok")


@app.tool(
    title="Create Cargoboard quotation",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def create_quotation(
    product: str,
    # Shipper information (required)
    shipper_reference: str,
    shipper_post_code: str,
    shipper_country_code: str,
    # Consignee information (required)
    consignee_reference: str,
    consignee_post_code: str,
    consignee_country_code: str,
    # Shipper information (optional)
    shipper_name: Optional[str] = None,
    shipper_street: Optional[str] = None,
    shipper_city: Optional[str] = None,
    shipper_contact_name: Optional[str] = None,
    shipper_contact_phone: Optional[str] = None,
    shipper_contact_email: Optional[str] = None,
    # Consignee information (optional)
    consignee_name: Optional[str] = None,
    consignee_street: Optional[str] = None,
    consignee_city: Optional[str] = None,
    consignee_contact_name: Optional[str] = None,
    consignee_contact_phone: Optional[str] = None,
    consignee_contact_email: Optional[str] = None,
    # Line items (simplified - single item for now)
    line_content: str = "General cargo",
    line_quantity: int = 1,
    line_package_type: str = "PA",
    line_length: float = 100.0,
    line_width: float = 80.0,
    line_height: float = 60.0,
    line_weight: float = 10.0,
    # Optional parameters
    customer_order_code: Optional[str] = None,
    coupon_code: Optional[str] = None,
    uit_code: Optional[str] = None,
    wants_export_declaration: bool = False,
    customs_tariff_quantity: Optional[float] = None,
    wants_climate_neutral_shipment: bool = True,
    wants_insurance: bool = False,
    incoterm: Optional[str] = None,
    is_supplying_company_or_receiving_customer: Optional[bool] = None,
    value_of_goods_amount: Optional[float] = None,
    value_of_goods_currency: Optional[str] = None,
    lines_pallet_bays: Optional[float] = None,
) -> Dict[str, Any]:
    """Create a new quotation request

    Args:
        product: Product type (DIRECT, EXPRESS, EXPRESS_8/10/12/16, FIX, FIX_8/10/12/16, STANDARD)
        shipper_reference: Shipper reference/identifier
        shipper_name: Shipper company name (optional for quotations)
        shipper_post_code: Shipper postal code
        shipper_country_code: Shipper country code (e.g., "DE", "FR")
        shipper_street: Shipper street address (optional)
        shipper_city: Shipper city (optional)
        shipper_contact_name: Shipper contact person name (optional)
        shipper_contact_phone: Shipper contact phone (optional)
        shipper_contact_email: Shipper contact email (optional)
        consignee_reference: Consignee reference/identifier
        consignee_name: Consignee company name (optional for quotations)
        consignee_post_code: Consignee postal code
        consignee_country_code: Consignee country code (e.g., "DE", "FR")
        consignee_street: Consignee street address (optional)
        consignee_city: Consignee city (optional)
        consignee_contact_name: Consignee contact person name (optional)
        consignee_contact_phone: Consignee contact phone (optional)
        consignee_contact_email: Consignee contact email (optional)
        line_content: Description of goods
        line_quantity: Number of packages
        line_package_type: Package type (PA=Package, FP=Flat Pallet, etc.)
        line_length: Package length in cm
        line_width: Package width in cm
        line_height: Package height in cm
        line_weight: Package weight in kg
        customer_order_code: Customer order code for documents
        coupon_code: Coupon code for special actions/discounts
        uit_code: UIT code
        wants_export_declaration: Whether to declare shipment for customs
        customs_tariff_quantity: Quantity of customs tariffs
        wants_climate_neutral_shipment: Whether to reduce environmental impact
        wants_insurance: Whether to insure the shipment
        incoterm: Incoterm (EXW, STANDARD, DDP, INVOICE_TO_RECIPIENT, DAP_CLEARED, DAP_UNCLEARED, FOB)
        is_supplying_company_or_receiving_customer: If customer is supplying company or receiving customer
        value_of_goods_amount: Value of goods for insurance/customs
        value_of_goods_currency: Currency of value of goods (EUR)
        lines_pallet_bays: Lines pallet bays of quotation
    """
    # Build shipper object
    shipper_data = {
        "reference": shipper_reference,
        "address": {
            "postCode": shipper_post_code,
            "countryCode": shipper_country_code,
        }
    }
    if shipper_name:
        shipper_data["name"] = shipper_name
    if shipper_street:
        shipper_data["address"]["street"] = shipper_street
    if shipper_city:
        shipper_data["address"]["city"] = shipper_city
    if shipper_contact_name:
        shipper_data["contactPerson"] = {
            "name": shipper_contact_name,
        }
        if shipper_contact_phone:
            shipper_data["contactPerson"]["phone"] = shipper_contact_phone
        if shipper_contact_email:
            shipper_data["contactPerson"]["email"] = shipper_contact_email

    # Build consignee object
    consignee_data = {
        "reference": consignee_reference,
        "address": {
            "postCode": consignee_post_code,
            "countryCode": consignee_country_code,
        }
    }
    if consignee_name:
        consignee_data["name"] = consignee_name
    if consignee_street:
        consignee_data["address"]["street"] = consignee_street
    if consignee_city:
        consignee_data["address"]["city"] = consignee_city
    if consignee_contact_name:
        consignee_data["contactPerson"] = {
            "name": consignee_contact_name,
        }
        if consignee_contact_phone:
            consignee_data["contactPerson"]["phone"] = consignee_contact_phone
        if consignee_contact_email:
            consignee_data["contactPerson"]["email"] = consignee_contact_email

    # Build line item
    line_data = {
        "content": line_content,
        "unitQuantity": line_quantity,
        "unitPackageType": line_package_type,
        "unitLength": line_length,
        "unitWidth": line_width,
        "unitHeight": line_height,
        "unitWeight": line_weight,
    }

    quotation_data = {
        "product": product,
        "shipper": shipper_data,
        "consignee": consignee_data,
        "lines": [line_data],
        "wantsExportDeclaration": wants_export_declaration,
        "wantsClimateNeutralShipment": wants_climate_neutral_shipment,
        "wantsInsurance": wants_insurance,
    }

    _add_optional_fields(quotation_data, 
        customer_order_code=customer_order_code,
        coupon_code=coupon_code,
        uit_code=uit_code,
        customs_tariff_quantity=customs_tariff_quantity,
        incoterm=incoterm,
        is_supplying_company_or_receiving_customer=is_supplying_company_or_receiving_customer,
        value_of_goods_amount=value_of_goods_amount,
        value_of_goods_currency=value_of_goods_currency,
        lines_pallet_bays=lines_pallet_bays
    )

    async with CargoboardClient(config) as client:
        return await client.post("/v1/quotations", json_data=quotation_data)


@app.tool(
    title="Create Cargoboard order",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
    enabled=_env_flag("CARGOBOARD_ENABLE_WRITE_TOOLS", default=False),
)
async def create_order(
    product: str,
    # Shipper information (required for orders)
    shipper_reference: str,
    shipper_name: str,
    shipper_post_code: str,
    shipper_country_code: str,
    shipper_street: str,
    shipper_city: str,
    # Consignee information (required for orders)
    consignee_reference: str,
    consignee_name: str,
    consignee_post_code: str,
    consignee_country_code: str,
    consignee_street: str,
    consignee_city: str,
    # Optional contact information
    shipper_contact_name: Optional[str] = None,
    shipper_contact_phone: Optional[str] = None,
    shipper_contact_email: Optional[str] = None,
    consignee_contact_name: Optional[str] = None,
    consignee_contact_phone: Optional[str] = None,
    consignee_contact_email: Optional[str] = None,
    # Line items (simplified - single item for now)
    line_content: str = "General cargo",
    line_quantity: int = 1,
    line_package_type: str = "PA",
    line_length: float = 100.0,
    line_width: float = 80.0,
    line_height: float = 60.0,
    line_weight: float = 10.0,
    # Optional parameters
    customer_order_code: Optional[str] = None,
    coupon_code: Optional[str] = None,
    uit_code: Optional[str] = None,
    wants_export_declaration: bool = False,
    customs_tariff_quantity: Optional[float] = None,
    wants_climate_neutral_shipment: bool = True,
    wants_insurance: bool = False,
    incoterm: Optional[str] = None,
    is_supplying_company_or_receiving_customer: Optional[bool] = None,
    value_of_goods_amount: Optional[float] = None,
    value_of_goods_currency: Optional[str] = None,
    tax_declaration: Optional[str] = None,
    lines_pallet_bays: Optional[float] = None,
) -> Dict[str, Any]:
    """Create a new order

    Args:
        product: Product type (DIRECT, EXPRESS, EXPRESS_8/10/12/16, FIX, FIX_8/10/12/16, STANDARD)
        shipper_reference: Shipper reference/identifier
        shipper_name: Shipper company name (required for orders)
        shipper_post_code: Shipper postal code
        shipper_country_code: Shipper country code (e.g., "DE", "FR")
        shipper_street: Shipper street address (required for orders)
        shipper_city: Shipper city (required for orders)
        shipper_contact_name: Shipper contact person name (optional)
        shipper_contact_phone: Shipper contact phone (optional)
        shipper_contact_email: Shipper contact email (optional)
        consignee_reference: Consignee reference/identifier
        consignee_name: Consignee company name (required for orders)
        consignee_post_code: Consignee postal code
        consignee_country_code: Consignee country code (e.g., "DE", "FR")
        consignee_street: Consignee street address (required for orders)
        consignee_city: Consignee city (required for orders)
        consignee_contact_name: Consignee contact person name (optional)
        consignee_contact_phone: Consignee contact phone (optional)
        consignee_contact_email: Consignee contact email (optional)
        line_content: Description of goods
        line_quantity: Number of packages
        line_package_type: Package type (PA=Package, FP=Flat Pallet, etc.)
        line_length: Package length in cm
        line_width: Package width in cm
        line_height: Package height in cm
        line_weight: Package weight in kg
        customer_order_code: Customer order code for documents
        coupon_code: Coupon code for special actions/discounts
        uit_code: UIT code
        wants_export_declaration: Whether to declare shipment for customs
        customs_tariff_quantity: Quantity of customs tariffs
        wants_climate_neutral_shipment: Whether to reduce environmental impact
        wants_insurance: Whether to insure the shipment
        incoterm: Incoterm (EXW, STANDARD, DDP, INVOICE_TO_RECIPIENT, DAP_CLEARED, DAP_UNCLEARED, FOB)
        is_supplying_company_or_receiving_customer: If customer is supplying company or receiving customer
        value_of_goods_amount: Value of goods for insurance/customs
        value_of_goods_currency: Currency of value of goods (EUR)
        tax_declaration: Tax declaration for Hungary/Romania shipments
        lines_pallet_bays: Lines pallet bays of order
    """
    # Build shipper object
    shipper_data = {
        "reference": shipper_reference,
        "name": shipper_name,
        "address": {
            "postCode": shipper_post_code,
            "countryCode": shipper_country_code,
            "street": shipper_street,
            "city": shipper_city,
        }
    }
    if shipper_contact_name:
        shipper_data["contactPerson"] = {
            "name": shipper_contact_name,
        }
        if shipper_contact_phone:
            shipper_data["contactPerson"]["phone"] = shipper_contact_phone
        if shipper_contact_email:
            shipper_data["contactPerson"]["email"] = shipper_contact_email

    # Build consignee object
    consignee_data = {
        "reference": consignee_reference,
        "name": consignee_name,
        "address": {
            "postCode": consignee_post_code,
            "countryCode": consignee_country_code,
            "street": consignee_street,
            "city": consignee_city,
        }
    }
    if consignee_contact_name:
        consignee_data["contactPerson"] = {
            "name": consignee_contact_name,
        }
        if consignee_contact_phone:
            consignee_data["contactPerson"]["phone"] = consignee_contact_phone
        if consignee_contact_email:
            consignee_data["contactPerson"]["email"] = consignee_contact_email

    # Build line item
    line_data = {
        "content": line_content,
        "unitQuantity": line_quantity,
        "unitPackageType": line_package_type,
        "unitLength": line_length,
        "unitWidth": line_width,
        "unitHeight": line_height,
        "unitWeight": line_weight,
    }

    order_data = {
        "product": product,
        "shipper": shipper_data,
        "consignee": consignee_data,
        "lines": [line_data],
        "wantsExportDeclaration": wants_export_declaration,
        "wantsClimateNeutralShipment": wants_climate_neutral_shipment,
        "wantsInsurance": wants_insurance,
    }

    _add_optional_fields(order_data,
        customer_order_code=customer_order_code,
        coupon_code=coupon_code,
        uit_code=uit_code,
        customs_tariff_quantity=customs_tariff_quantity,
        incoterm=incoterm,
        is_supplying_company_or_receiving_customer=is_supplying_company_or_receiving_customer,
        value_of_goods_amount=value_of_goods_amount,
        value_of_goods_currency=value_of_goods_currency,
        tax_declaration=tax_declaration,
        lines_pallet_bays=lines_pallet_bays
    )

    async with CargoboardClient(config) as client:
        return await client.post("/v1/order", json_data=order_data)


@app.tool(
    title="Get Cargoboard quotation",
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
async def get_quotation(quotation_id: str) -> Dict[str, Any]:
    """Get detailed information for a specific quotation

    Args:
        quotation_id: The unique identifier of the quotation
    """
    safe_quotation_id = _validated_resource_id(quotation_id)
    async with CargoboardClient(config) as client:
        return await client.get(f"/v1/quotations/{safe_quotation_id}")


@app.tool(
    title="Get Cargoboard order",
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
async def get_order(order_id: str) -> Dict[str, Any]:
    """Get detailed information for a specific order

    Args:
        order_id: The unique identifier of the order
    """
    safe_order_id = _validated_resource_id(order_id)
    async with CargoboardClient(config) as client:
        return await client.get(f"/v1/orders/{safe_order_id}")


@app.tool(
    title="Get Cargoboard shipment labels",
    annotations={"readOnlyHint": True, "openWorldHint": True},
)
async def print_shipment_labels(order_id: str) -> Dict[str, Any]:
    """Generate and print shipment labels for an order

    Args:
        order_id: The unique identifier of the order
    """
    safe_order_id = _validated_resource_id(order_id)
    async with CargoboardClient(config) as client:
        return await client.get(
            f"/v1/orders/{safe_order_id}/print-shipment-labels"
        )


if __name__ == "__main__":
    try:
        config.validate()
        port = _port_from_env()
        logger.info("Starting Cargoboard MCP server on configured HTTP port")
        app.run(
            transport="http",
            host="0.0.0.0",
            port=port,
            path="/mcp",
            show_banner=False,
            stateless_http=True,
            uvicorn_config={
                "access_log": False,
                "server_header": False,
            },
        )
    except Exception:
        logger.error("Cargoboard MCP server failed to start")
        raise SystemExit(1) from None
