import pytest
from uav_downloader.sites.missav import SiteMissAV


@pytest.mark.parametrize('listing', ['dm541/fc2', 'dm4854130/1pondo'])
def test_numbered_category_is_not_a_video(listing):
    assert SiteMissAV.validate_url('https://missav.ai/' + listing) is None
    assert SiteMissAV.validate_url('https://missav.ai/fc2-12345') == 'fc2-12345'
