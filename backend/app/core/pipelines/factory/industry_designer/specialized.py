"""Industry-specialized Designers for V2.1.0 W3.

Each subclass extends GeneralDesigner with industry-specific defaults:
  - extra guardrails (compliance / PII / domain-specific filters)
  - extra shared_context (customer_id / patient_id / audit_trail / ...)

Pattern: subclass declares the extras as class attrs; the base method
augments the GeneralDesigner spec after extraction. No re-prompting,
no LLM cost overhead — pure metadata injection.

Per [[资产中心/横切-行业分类]]:
  02 金融 (FinanceDesigner)
  06 医疗 (MedicalDesigner)
  08 政务 (GovernmentDesigner)
  09 零售 (RetailDesigner)

V2.1.0 W3+: V2.1.1+ adds Manufacturing / Energy / Transportation /
Education / Media / Telecom / SmartCity.
"""
from __future__ import annotations

from app.core.pipelines.factory.industry.interface import IndustryClassification
from app.core.pipelines.factory.industry_designer.general import GeneralDesigner
from app.core.pipelines.factory.ir.multi_agent import (
    GuardrailSpec,
    MultiAgentSpec,
    SharedContextField,
)


class _IndustrySpecializedDesigner(GeneralDesigner):
    """Base: extends GeneralDesigner with industry-specific guardrails + context.

    Concrete subclasses override:
      industry_code: '02'..'12' (must NOT be '01' which is General)
      extra_guardrails: list[GuardrailSpec]
      extra_shared_context: list[SharedContextField]
    """

    industry_code: str = "OVERRIDE"
    extra_guardrails: list[GuardrailSpec] = []
    extra_shared_context: list[SharedContextField] = []

    async def design_multi_agent(
        self, nl: str, classification: IndustryClassification
    ) -> MultiAgentSpec:
        spec = await super().design_multi_agent(nl, classification)

        # Inject industry guardrails (skip duplicates by kind)
        existing_kinds = {g.kind for g in spec.guardrails}
        for guardrail in self.extra_guardrails:
            if guardrail.kind not in existing_kinds:
                spec.guardrails.append(guardrail)
                existing_kinds.add(guardrail.kind)

        # Inject industry shared_context (skip duplicates by name)
        existing_names = {c.name for c in spec.shared_context}
        for ctx_field in self.extra_shared_context:
            if ctx_field.name not in existing_names:
                spec.shared_context.append(ctx_field)
                existing_names.add(ctx_field.name)

        # Re-validate after augmentation (defensive — augmentation should be safe)
        issues = spec.validate_graph()
        if issues:
            raise ValueError(
                f"{type(self).__name__} produced spec with structural issues "
                f"after augmentation: {issues}"
            )
        return spec


class FinanceDesigner(_IndustrySpecializedDesigner):
    """Industry 02 - 金融（银行/保险/证券）.

    Guardrails: KYC/AML compliance + PII masking.
    Shared context: customer_id / account_id / audit_trail_id.
    """

    industry_code = "02"
    extra_guardrails = [
        GuardrailSpec(
            kind="compliance",
            description="Banking regulatory compliance: KYC/AML check on identity-sensitive turns; refuse advice without proper disclosure",
            blocking=True,
        ),
        GuardrailSpec(
            kind="pii",
            description="Mask account numbers and personal identifiers in all output; never echo full account number",
            blocking=True,
        ),
    ]
    extra_shared_context = [
        SharedContextField(
            name="customer_id",
            type="string",
            description="Bank customer identifier for current session",
        ),
        SharedContextField(
            name="account_id",
            type="string",
            description="Active account reference (masked in output)",
        ),
        SharedContextField(
            name="audit_trail_id",
            type="string",
            description="Compliance audit trail reference for all actions",
        ),
    ]


class MedicalDesigner(_IndustrySpecializedDesigner):
    """Industry 06 - 医疗（医院 / 诊所）.

    Guardrails: PII (HIPAA-style) + compliance refusal for diagnosis.
    Shared context: patient_id / encounter_id.
    """

    industry_code = "06"
    extra_guardrails = [
        GuardrailSpec(
            kind="pii",
            description="HIPAA-aligned PII handling: never echo patient names/IDs in cleartext; redact medical records before forwarding",
            blocking=True,
        ),
        GuardrailSpec(
            kind="compliance",
            description="Refuse to provide medical diagnosis or treatment recommendations; always defer to licensed clinician",
            blocking=True,
        ),
    ]
    extra_shared_context = [
        SharedContextField(
            name="patient_id",
            type="string",
            description="Patient identifier (masked in output)",
        ),
        SharedContextField(
            name="encounter_id",
            type="string",
            description="Current clinical encounter reference",
        ),
    ]


class GovernmentDesigner(_IndustrySpecializedDesigner):
    """Industry 08 - 政务（市级热线 / 部门服务）.

    Guardrails: PII + compliance (refuse policy interpretation as authoritative).
    Shared context: citizen_id / case_number / department.
    """

    industry_code = "08"
    extra_guardrails = [
        GuardrailSpec(
            kind="pii",
            description="Mask citizen ID (身份证号) and contact info; never echo full ID in any output",
            blocking=True,
        ),
        GuardrailSpec(
            kind="compliance",
            description="Provide informational guidance only; never claim to be the authoritative policy interpreter",
            blocking=True,
        ),
    ]
    extra_shared_context = [
        SharedContextField(
            name="citizen_id",
            type="string",
            description="Citizen identifier (masked in output)",
        ),
        SharedContextField(
            name="case_number",
            type="string",
            description="Service case number for tracking",
        ),
        SharedContextField(
            name="responsible_department",
            type="string",
            description="Government department handling this case",
        ),
    ]


class RetailDesigner(_IndustrySpecializedDesigner):
    """Industry 09 - 零售（电商 / 门店）.

    Guardrails: PII (no full credit card / address echo) + relevance.
    Shared context: customer_id / order_id / cart_id.
    """

    industry_code = "09"
    extra_guardrails = [
        GuardrailSpec(
            kind="pii",
            description="Mask credit card numbers, full mailing addresses, and phone numbers in all output",
            blocking=True,
        ),
    ]
    extra_shared_context = [
        SharedContextField(
            name="customer_id",
            type="string",
            description="Retail customer identifier for session",
        ),
        SharedContextField(
            name="order_id",
            type="string",
            description="Active order reference (if any)",
        ),
        SharedContextField(
            name="cart_id",
            type="string",
            description="Active shopping cart reference",
        ),
    ]
