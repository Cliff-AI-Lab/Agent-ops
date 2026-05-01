from __future__ import annotations

REQUIREMENT_SPEC_EXAMPLES = [
    {
        "product_name": "Sales Copilot",
        "target_users": ["field sales", "sales ops"],
        "core_pages": ["dashboard", "deal review", "account detail"],
        "reference_brands": ["HubSpot", "Notion"],
        "special_requirements": ["mobile first", "CN localization"],
    },
    {
        "product_name": "Clinic Intake",
        "target_users": ["front desk", "patients"],
        "core_pages": ["intake form", "queue board"],
        "reference_brands": ["Mayo Clinic"],
        "special_requirements": ["large text", "privacy notice"],
    },
    {
        "product_name": "Warehouse Pulse",
        "target_users": ["shift managers"],
        "core_pages": ["ops overview", "incident log", "staff board"],
        "reference_brands": [],
        "special_requirements": ["offline fallback"],
    },
]

PAGE_BLUEPRINT_EXAMPLES = [
    {
        "route": "/",
        "title": "Overview",
        "components": ["hero", "card-grid"],
        "data_fields": ["headline", "summary", "metrics"],
    },
    {
        "route": "/orders",
        "title": "Order Queue",
        "components": ["table", "form"],
        "data_fields": ["order_id", "status", "owner"],
    },
    {
        "route": "/customers",
        "title": "Customer List",
        "components": ["list", "card-grid"],
        "data_fields": ["name", "segment", "health_score"],
    },
]

BRAND_TOKENS_EXAMPLES = [
    {"primary": "#0F62FE", "font": "IBM Plex Sans", "radius": "md"},
    {"primary": "#D9480F", "font": "Source Sans 3", "radius": "sm"},
    {"primary": "#1A936F", "font": "Noto Sans SC", "radius": "lg"},
]

UI_BLUEPRINT_EXAMPLES = [
    {
        "pages": [
            {
                "route": "/",
                "title": "Overview",
                "components": ["hero", "card-grid"],
                "data_fields": ["headline", "metrics"],
            }
        ],
        "brand": {"primary": "#0F62FE", "font": "IBM Plex Sans", "radius": "md"},
    },
    {
        "pages": [
            {
                "route": "/intake",
                "title": "Patient Intake",
                "components": ["form"],
                "data_fields": ["patient_name", "insurance_id"],
            }
        ],
        "brand": {"primary": "#D9480F", "font": "Source Sans 3", "radius": "sm"},
    },
    {
        "pages": [
            {
                "route": "/ops",
                "title": "Operations Board",
                "components": ["table", "list"],
                "data_fields": ["shift", "alerts", "throughput"],
            }
        ],
        "brand": {"primary": "#1A936F", "font": "Noto Sans SC", "radius": "lg"},
    },
]

FILE_ENTRY_EXAMPLES = [
    {"path": "src/main.ts", "content": "console.log('hello');"},
    {"path": "app/page.tsx", "content": "export default function Page() { return null; }"},
    {"path": "README.md", "content": "# Generated App"},
]

CODE_ARTIFACT_EXAMPLES = [
    {
        "files": [{"path": "src/main.ts", "content": "console.log('hello');"}],
        "dependencies": {"react": "^18.3.0"},
        "entrypoint": "src/main.ts",
    },
    {
        "files": [{"path": "app/page.tsx", "content": "export default function Page() { return null; }"}],
        "dependencies": {"next": "^14.2.0"},
        "entrypoint": "app/page.tsx",
    },
    {
        "files": [{"path": "server.py", "content": "print('serve')"}],
        "dependencies": {"fastapi": "^0.115.0"},
        "entrypoint": "server.py",
    },
]
