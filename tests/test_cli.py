import unittest

from tracker.cli import extract_asin


class AsinTests(unittest.TestCase):
    def test_links(self):
        for link in [
            "https://www.amazon.eg/dp/B0CHX1W1XY",
            "https://www.amazon.eg/-/en/Some-Product-Name/dp/B0CHX1W1XY/ref=sr_1_3?keywords=x",
            "https://www.amazon.com/gp/product/B0CHX1W1XY?th=1",
            "b0chx1w1xy",
        ]:
            self.assertEqual(extract_asin(link), "B0CHX1W1XY", link)

    def test_bad_link(self):
        with self.assertRaises(ValueError):
            extract_asin("https://www.amazon.eg/s?k=kettle")


if __name__ == "__main__":
    unittest.main()
