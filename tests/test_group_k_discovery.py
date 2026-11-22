"""Verify representative Group K wordings through the real matching and verification code.

Profile discovery is covered in test_perception_expectations.py; versioned storage is
covered in test_expectations.py. The profile here is synthetic input, not a discovery result.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ftf.expectations.library import (
    ElementBasis,
    ElementChecked,
    ElementProfile,
    ExpectedElement,
    KindProfile,
    PageKind,
    TypicalWording,
)
from ftf.expectations.verification import search_for_element_in_text, verify_element


class TestGroupKElements:
    """Test that expected Group K elements are discoverable and verifiable."""

    @staticmethod
    def build_investment_profile() -> KindProfile:
        """Build an investment offer profile with Group K elements."""
        kind = PageKind(
            kind_id="kind.1_investment_offer",
            name="investment offer",
            description="Solicits investment in a scheme",
            vertical="Investment / trading",
        )

        elements = [
            ElementProfile(
                element=ExpectedElement(
                    name="legal_entity_name",
                    description="Legal entity name and registered address",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Spółka z ograniczoną odpowiedzialnością"),
                        TypicalWording(text="Sp. z o.o."),
                        TypicalWording(text="siedziba"),
                    ],
                    typical_location="footer or legal section",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="knf_authorization",
                    description="KNF authorisation or registry reference",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Komisja Nadzoru Finansowego"),
                        TypicalWording(text="KNF"),
                        TypicalWording(text="rejestr"),
                    ],
                    typical_location="terms or regulatory section",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="risk_warning",
                    description="Prominent warning about investment risks",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Oświadczenie o ryzyku"),
                        TypicalWording(text="inwestycja wiąże się z ryzykiem"),
                        TypicalWording(text="możliwa utrata kapitału"),
                    ],
                    typical_location="header or prominently displayed",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="terms_and_privacy",
                    description="Terms of service and privacy policy",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Regulamin"),
                        TypicalWording(text="Polityka prywatności"),
                        TypicalWording(text="Terms of Service"),
                    ],
                    typical_location="footer",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="data_controller_consent",
                    description="Data controller clause and consent next to form",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Administrator danych"),
                        TypicalWording(text="wyrażam zgodę"),
                        TypicalWording(text="Oświadczam, iż przeczytałem"),
                    ],
                    typical_location="next to form fields",
                ),
                promoted=True,
            ),
        ]

        return KindProfile(
            kind=kind,
            elements=elements,
            generated_from="model_knowledge",
            created_at=datetime.now(UTC),
        )

    def test_elements_are_verifiable_in_text(self):
        """Test that profile elements can be verified against page text."""
        profile = self.build_investment_profile()

        # Sample investment page text with some elements
        page_text = """
        Investment Opportunity
        
        Risk Warning: Oświadczenie o ryzyku - investment carries significant risk
        
        Company Details:
        Sp. z o.o.
        siedziba: ul. Marszałkowska 1, Warszawa
        
        KNF Registration: Registered with Komisja Nadzoru Finansowego
        
        Footer:
        Polityka prywatności | Regulamin
        Administrator danych: privacy@example.com
        Wyrażam zgodę na przetwarzanie danych
        """

        for ep in profile.elements:
            found, wording = search_for_element_in_text(ep.element, page_text)
            assert found, ep.element.name  # every Group K element above has a wording in the text
            present = verify_element(ep.element, ElementChecked.PRESENT, wording, page_text)
            assert present.verified, (ep.element.name, present.reason)
            absent = verify_element(ep.element, ElementChecked.ABSENT, None, page_text)
            assert not absent.verified, ep.element.name
        missing = ExpectedElement(
            name="licence_number",
            description="Licence number",
            typical_wordings=[TypicalWording(text="numer licencji")],
        )
        assert verify_element(missing, ElementChecked.ABSENT, None, page_text).verified
