import sys
import unittest

sys.path.insert(0, '/home/habithon1/Documents/HabithonAI/src')

from anonymization.adapters.detectors.regex_detector import RegexDetector
from anonymization.adapters.detectors.presidio_detector import PresidioDetector
from anonymization.app.detect import detect_all


class TestPresidioFallback(unittest.TestCase):
    def test_detect_all_with_presidio_present_or_missing(self):
        text = 'Email: alice@example.com Phone: +1 555-123-4567'
        detectors = [RegexDetector(), PresidioDetector(languages=None)]
        result = detect_all(text, detectors, language='en')
        self.assertIn('alice@example.com', result.text)
        # It should detect the email via regex or presidio
        self.assertTrue(any(e.type in ('EMAIL', 'EMAIL_ADDRESS') and e.value == 'alice@example.com' for e in result.entities))


if __name__ == '__main__':
    unittest.main()
