import unittest

from amocrm_service.site_forms import parse_site_lead_payload


class SiteFormsTest(unittest.TestCase):
    def test_parse_site_lead_payload_accepts_common_fields(self):
        form = parse_site_lead_payload({
            "name": " Иван ",
            "phone": " +79990000000 ",
            "email": " ivan@example.com ",
            "message": " Хочу консультацию ",
            "source": " landing ",
            "page_url": " https://example.com/ ",
            "price": "15000",
        })

        self.assertEqual(form.name, "Иван")
        self.assertEqual(form.phone, "+79990000000")
        self.assertEqual(form.email, "ivan@example.com")
        self.assertEqual(form.message, "Хочу консультацию")
        self.assertEqual(form.source, "landing")
        self.assertEqual(form.page_url, "https://example.com/")
        self.assertEqual(form.price, 15000)
        self.assertEqual(form.contact_name, "Иван")
        self.assertEqual(form.lead_name, "Заявка с сайта: Иван")

    def test_parse_site_lead_payload_accepts_aliases(self):
        form = parse_site_lead_payload({
            "tel": "+79990000000",
            "comment": "Перезвонить",
            "url": "https://example.com/pricing",
        })

        self.assertEqual(form.phone, "+79990000000")
        self.assertEqual(form.message, "Перезвонить")
        self.assertEqual(form.page_url, "https://example.com/pricing")

    def test_parse_site_lead_payload_requires_contact_data(self):
        with self.assertRaisesRegex(ValueError, "name, phone or email is required"):
            parse_site_lead_payload({"message": "empty"})


if __name__ == "__main__":
    unittest.main()
