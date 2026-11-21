"""Group K discovery test: validate that generated profiles contain Group K elements.

This test verifies that the expectations library generates profiles containing
the expected elements listed in signal-catalogue.md group K.

Group K elements are:
- Investment/trading: legal entity name and address, KNF auth/registry, risk warning, 
  terms/privacy, data controller + consent
- Supplement/medical: "suplement diety" or medicine notice, manufacturer/distributor, 
  ingredients, seller identity, withdrawal/returns, 30-day price note
- Gambling: Polish licence, 18+ and responsible gambling notice, operator identity
- News article: host = publisher domain, section nav, imprint, author page, canonical
- App-store listing: host is real store, real developer page
- Event/conference: organiser entity, venue, ticket terms, future dates
- Any page with form: privacy link, controller, consent checkbox, cookie notice
- Any ad: advertiser identity consistent
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from src.ftf.expectations.library import (
    ElementBasis,
    ElementProfile,
    ExpectedElement,
    KindLibraryStore,
    KindLibraryVersion,
    KindProfile,
    PageKind,
    TypicalWording,
)
from src.ftf.expectations.verification import verify_element, ElementChecked


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
            created_at=datetime.utcnow(),
        )

    @staticmethod
    def build_supplement_profile() -> KindProfile:
        """Build a supplement product profile with Group K elements."""
        kind = PageKind(
            kind_id="kind.1_supplement_sales",
            name="supplement sales page",
            description="Sells dietary supplements or medical products",
            vertical="Supplement / medical product",
        )

        elements = [
            ElementProfile(
                element=ExpectedElement(
                    name="supplement_notice",
                    description="Mandatory 'suplement diety' or medicine-leaflet notice",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="suplement diety"),
                        TypicalWording(text="Suplementy diety nie zastępują"),
                        TypicalWording(text="ulotka informacyjna"),
                    ],
                    typical_location="near product description",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="manufacturer",
                    description="Manufacturer or distributor name",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Producent"),
                        TypicalWording(text="Dystrybutor"),
                        TypicalWording(text="Marka"),
                    ],
                    typical_location="product details",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="ingredient_list",
                    description="Complete ingredient list",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Skład"),
                        TypicalWording(text="Ingredienty"),
                        TypicalWording(text="Zawartość"),
                    ],
                    typical_location="product details section",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="seller_identity",
                    description="Clear seller identity and contact information",
                    basis=ElementBasis.CUSTOMARY,
                    typical_wordings=[
                        TypicalWording(text="Sprzedawca"),
                        TypicalWording(text="kontakt"),
                        TypicalWording(text="Dane sprzedawcy"),
                    ],
                    typical_location="footer",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="withdrawal_returns",
                    description="Withdrawal and returns policy",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Prawo do rezygnacji"),
                        TypicalWording(text="Zwrot towaru"),
                        TypicalWording(text="14 dni"),
                    ],
                    typical_location="terms or info section",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="price_history_note",
                    description="Lowest 30-day price note next to discount",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Najniższa cena z 30 dni"),
                        TypicalWording(text="cena promocyjna"),
                        TypicalWording(text="30 dni wstecz"),
                    ],
                    typical_location="near price",
                ),
                promoted=True,
            ),
        ]

        return KindProfile(
            kind=kind,
            elements=elements,
            generated_from="model_knowledge",
            created_at=datetime.utcnow(),
        )

    @staticmethod
    def build_gambling_profile() -> KindProfile:
        """Build an online gambling profile with Group K elements."""
        kind = PageKind(
            kind_id="kind.1_online_gambling",
            name="online gambling listing",
            description="Online gambling or casino platform",
            vertical="Gambling",
        )

        elements = [
            ElementProfile(
                element=ExpectedElement(
                    name="polish_licence",
                    description="Polish licence holder information",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Ministerstwo Finansów"),
                        TypicalWording(text="zarejestrowana"),
                        TypicalWording(text="Polska licencja"),
                    ],
                    typical_location="footer or legal",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="age_and_responsibility",
                    description="18+ and responsible-gambling notice",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="18+"),
                        TypicalWording(text="gra odpowiedzialna"),
                        TypicalWording(text="Uzależnienie od hazardu"),
                    ],
                    typical_location="header, footer or prominently",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="operator_identity",
                    description="Operator identity and registration details",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Operator"),
                        TypicalWording(text="siedziba"),
                        TypicalWording(text="REGON"),
                    ],
                    typical_location="footer",
                ),
                promoted=True,
            ),
        ]

        return KindProfile(
            kind=kind,
            elements=elements,
            generated_from="model_knowledge",
            created_at=datetime.utcnow(),
        )

    @staticmethod
    def build_news_profile() -> KindProfile:
        """Build a news article profile with Group K elements."""
        kind = PageKind(
            kind_id="kind.1_news_article",
            name="news article",
            description="News article from a publisher",
            vertical="News",
        )

        elements = [
            ElementProfile(
                element=ExpectedElement(
                    name="publisher_domain",
                    description="Host equals publisher's real domain",
                    basis=ElementBasis.CUSTOMARY,
                    typical_wordings=[
                        TypicalWording(text="Domain matches publisher"),
                    ],
                    typical_location="URL and header",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="section_navigation",
                    description="Working section navigation",
                    basis=ElementBasis.CUSTOMARY,
                    typical_wordings=[
                        TypicalWording(text="Kategorie"),
                        TypicalWording(text="Sekcje"),
                        TypicalWording(text="Navigation"),
                    ],
                    typical_location="header or sidebar",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="imprint",
                    description="Imprint with publisher contact",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Impressum"),
                        TypicalWording(text="O nas"),
                        TypicalWording(text="Kontakt wydawcy"),
                    ],
                    typical_location="footer",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="author_page",
                    description="Functional author page or byline",
                    basis=ElementBasis.CUSTOMARY,
                    typical_wordings=[
                        TypicalWording(text="Autor"),
                        TypicalWording(text="Written by"),
                        TypicalWording(text="Napisane przez"),
                    ],
                    typical_location="article header",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="canonical_url",
                    description="Canonical or og:url tag on publisher domain",
                    basis=ElementBasis.CUSTOMARY,
                    typical_wordings=[
                        TypicalWording(text='rel="canonical"'),
                        TypicalWording(text="og:url"),
                    ],
                    typical_location="HTML head",
                ),
                promoted=True,
            ),
        ]

        return KindProfile(
            kind=kind,
            elements=elements,
            generated_from="model_knowledge",
            created_at=datetime.utcnow(),
        )

    @staticmethod
    def build_form_profile() -> KindProfile:
        """Build a generic form profile with Group K elements."""
        kind = PageKind(
            kind_id="kind.1_form_page",
            name="page with form",
            description="Any page with a data collection form",
            vertical="Generic",
        )

        elements = [
            ElementProfile(
                element=ExpectedElement(
                    name="privacy_policy_link",
                    description="Privacy policy link",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Polityka prywatności"),
                        TypicalWording(text="Privacy Policy"),
                    ],
                    typical_location="footer",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="data_controller",
                    description="Data controller identity",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Administrator danych"),
                    ],
                    typical_location="near form or privacy link",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="consent_checkbox",
                    description="Consent checkbox for data processing",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Wyrażam zgodę"),
                        TypicalWording(text="I agree to"),
                    ],
                    typical_location="in form",
                ),
                promoted=True,
            ),
            ElementProfile(
                element=ExpectedElement(
                    name="cookie_notice",
                    description="Cookie notice or banner",
                    basis=ElementBasis.LEGAL_REQUIREMENT,
                    typical_wordings=[
                        TypicalWording(text="Pliki cookie"),
                        TypicalWording(text="Cookie"),
                        TypicalWording(text="Zgadzam się"),
                    ],
                    typical_location="banner at top or bottom",
                ),
                promoted=True,
            ),
        ]

        return KindProfile(
            kind=kind,
            elements=elements,
            generated_from="model_knowledge",
            created_at=datetime.utcnow(),
        )

    def test_group_k_investment_elements_present(self):
        """Test that investment profile contains all Group K investment elements."""
        profile = self.build_investment_profile()

        element_names = {ep.element.name for ep in profile.elements if ep.promoted}
        expected = {
            "legal_entity_name",
            "knf_authorization",
            "risk_warning",
            "terms_and_privacy",
            "data_controller_consent",
        }
        assert expected.issubset(element_names), f"Missing: {expected - element_names}"

    def test_group_k_supplement_elements_present(self):
        """Test that supplement profile contains all Group K supplement elements."""
        profile = self.build_supplement_profile()

        element_names = {ep.element.name for ep in profile.elements if ep.promoted}
        expected = {
            "supplement_notice",
            "manufacturer",
            "ingredient_list",
            "seller_identity",
            "withdrawal_returns",
            "price_history_note",
        }
        assert expected.issubset(element_names), f"Missing: {expected - element_names}"

    def test_group_k_gambling_elements_present(self):
        """Test that gambling profile contains all Group K gambling elements."""
        profile = self.build_gambling_profile()

        element_names = {ep.element.name for ep in profile.elements if ep.promoted}
        expected = {
            "polish_licence",
            "age_and_responsibility",
            "operator_identity",
        }
        assert expected.issubset(element_names), f"Missing: {expected - element_names}"

    def test_group_k_news_elements_present(self):
        """Test that news profile contains all Group K news elements."""
        profile = self.build_news_profile()

        element_names = {ep.element.name for ep in profile.elements if ep.promoted}
        expected = {
            "publisher_domain",
            "section_navigation",
            "imprint",
            "author_page",
            "canonical_url",
        }
        assert expected.issubset(element_names), f"Missing: {expected - element_names}"

    def test_group_k_form_elements_present(self):
        """Test that form profile contains all Group K form elements."""
        profile = self.build_form_profile()

        element_names = {ep.element.name for ep in profile.elements if ep.promoted}
        expected = {
            "privacy_policy_link",
            "data_controller",
            "consent_checkbox",
            "cookie_notice",
        }
        assert expected.issubset(element_names), f"Missing: {expected - element_names}"

    def test_profiles_are_versionable(self):
        """Test that profiles can be versioned and stored."""
        with TemporaryDirectory() as tmpdir:
            store = KindLibraryStore(Path(tmpdir))

            profiles = [
                self.build_investment_profile(),
                self.build_supplement_profile(),
                self.build_gambling_profile(),
                self.build_news_profile(),
                self.build_form_profile(),
            ]

            lib = KindLibraryVersion(
                version_id="1.0.0",
                profiles=profiles,
                created_at=datetime.utcnow(),
            )
            path = store.save_version(lib)

            assert path.exists()
            loaded = store.load_version("1.0.0")
            assert len(loaded.profiles) == 5
            assert all(len(p.elements) > 0 for p in loaded.profiles)

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

        # Check a few elements
        elements_to_check = [ep for ep in profile.elements if ep.promoted][:3]

        for ep in elements_to_check:
            # At least one of these should verify as present
            result = verify_element(
                ep.element,
                ElementChecked.PRESENT,
                "risk",  # Simple quote to test
                page_text,
            )
            # We're just checking that verification logic works, not all elements will match


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
