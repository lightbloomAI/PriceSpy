import re
from typing import Optional, Tuple
from urllib.parse import urlparse

# Country code to name mapping
COUNTRY_NAMES = {
    'AT': 'Austria', 'BE': 'Belgium', 'BG': 'Bulgaria', 'HR': 'Croatia',
    'CY': 'Cyprus', 'CZ': 'Czechia', 'DK': 'Denmark', 'EE': 'Estonia',
    'FI': 'Finland', 'FR': 'France', 'DE': 'Germany', 'GR': 'Greece',
    'HU': 'Hungary', 'IE': 'Ireland', 'IT': 'Italy', 'LV': 'Latvia',
    'LT': 'Lithuania', 'LU': 'Luxembourg', 'MT': 'Malta', 'NL': 'Netherlands',
    'PL': 'Poland', 'PT': 'Portugal', 'RO': 'Romania', 'SK': 'Slovakia',
    'SI': 'Slovenia', 'ES': 'Spain', 'SE': 'Sweden',
    # Non-EU European
    'UK': 'United Kingdom', 'GB': 'United Kingdom', 'CH': 'Switzerland', 'NO': 'Norway',
    # Other
    'US': 'United States', 'CA': 'Canada', 'AU': 'Australia', 'NZ': 'New Zealand',
    'JP': 'Japan', 'CN': 'China', 'KR': 'South Korea',
}

# EU country codes
EU_COUNTRIES = {
    'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR',
    'HU', 'IE', 'IT', 'LV', 'LT', 'LU', 'MT', 'NL', 'PL', 'PT', 'RO', 'SK',
    'SI', 'ES', 'SE'
}

# Domain TLD to country code mapping
TLD_TO_COUNTRY = {
    '.at': 'AT', '.be': 'BE', '.bg': 'BG', '.hr': 'HR', '.cy': 'CY',
    '.cz': 'CZ', '.dk': 'DK', '.ee': 'EE', '.fi': 'FI', '.fr': 'FR',
    '.de': 'DE', '.gr': 'GR', '.hu': 'HU', '.ie': 'IE', '.it': 'IT',
    '.lv': 'LV', '.lt': 'LT', '.lu': 'LU', '.mt': 'MT', '.nl': 'NL',
    '.pl': 'PL', '.pt': 'PT', '.ro': 'RO', '.sk': 'SK', '.si': 'SI',
    '.es': 'ES', '.se': 'SE',
    '.uk': 'UK', '.co.uk': 'UK', '.ch': 'CH', '.no': 'NO',
    '.us': 'US', '.ca': 'CA', '.au': 'AU', '.nz': 'NZ',
    '.jp': 'JP', '.cn': 'CN', '.kr': 'KR',
    '.com': None, '.eu': None,  # Generic, need more context
}

# Known retailers and their countries
KNOWN_RETAILERS = {
    'amazon.com': 'US', 'amazon.de': 'DE', 'amazon.fr': 'FR', 'amazon.it': 'IT',
    'amazon.es': 'ES', 'amazon.co.uk': 'UK', 'amazon.nl': 'NL',
    'ebay.com': 'US', 'ebay.de': 'DE', 'ebay.co.uk': 'UK',
    'globetrotter': 'DE', 'globetrotter.de': 'DE',
    'bergfreunde': 'DE', 'bergfreunde.de': 'DE',
    'zalando': 'DE', 'zalando.de': 'DE',
    'decathlon': 'FR',
    'basecamp': 'DE',  # German outdoor retailer
    'nautica urban': 'IT',  # Italian retailer
    'viglietti sport': 'IT',  # Italian retailer
    'outside sports nz': 'NZ',
    'outside sports': 'NZ',
    'trident fly fishing': 'US',
}


def detect_country(retailer: str, url: str = "") -> Tuple[Optional[str], Optional[str]]:
    """
    Detect country from retailer name and URL.
    Returns (country_code, country_name) or (None, None) if unknown.
    """
    retailer_lower = retailer.lower().strip()

    # Check known retailers first
    for known, country_code in KNOWN_RETAILERS.items():
        if known in retailer_lower:
            return country_code, COUNTRY_NAMES.get(country_code)

    # Check for country code in retailer name (e.g., "Outside Sports NZ")
    # Look for 2-letter codes at the end
    match = re.search(r'\b([A-Z]{2})$', retailer)
    if match:
        code = match.group(1)
        if code in COUNTRY_NAMES:
            return code, COUNTRY_NAMES[code]

    # Check URL domain TLD
    if url:
        try:
            # Handle Google Shopping redirect URLs - extract original domain if possible
            if 'google.com' in url:
                # Try to find retailer domain in URL params
                pass  # Google URLs don't help with country detection
            else:
                parsed = urlparse(url)
                domain = parsed.netloc.lower()

                # Check for country TLDs
                for tld, country_code in TLD_TO_COUNTRY.items():
                    if domain.endswith(tld) and country_code:
                        return country_code, COUNTRY_NAMES.get(country_code)
        except Exception:
            pass

    # Check for country names in retailer
    for code, name in COUNTRY_NAMES.items():
        if name.lower() in retailer_lower:
            return code, name

    return None, None


def is_eu_country(country_code: Optional[str]) -> bool:
    """Check if a country code is in the EU."""
    if not country_code:
        return False
    return country_code.upper() in EU_COUNTRIES


def should_include_retailer(country_code: Optional[str], region: str) -> bool:
    """
    Check if a retailer should be included based on product region.
    - 'eu': Only EU countries
    - 'hu': Hungary + EU countries
    - 'worldwide': All countries
    """
    if region == 'worldwide':
        return True

    if not country_code:
        # Unknown country - include it but user can exclude manually
        return True

    if region == 'eu' or region == 'hu':
        return is_eu_country(country_code)

    return True
