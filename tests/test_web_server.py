"""Local web endpoints, byte ranges, privacy and unavailable videos; no external services."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from newscard.web_server import BriefingServer, parse_range


@pytest.fixture
def site(tmp_path):
    data = tmp_path / 'data'
    web = tmp_path / 'web'
    (data / 'video').mkdir(parents=True)
    web.mkdir()
    (web / 'index.html').write_text('브리핑', encoding='utf-8')
    (data / 'top_events.json').write_text('private article body', encoding='utf-8')
    server = BriefingServer(('127.0.0.1', 0), data, web)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield data, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def get(url, *, headers=None, method='GET'):
    return urlopen(Request(url, headers=headers or {}, method=method), timeout=3)


def test_no_video_and_missing_video(site):
    _, url = site
    with get(url + '/api/videos') as res:
        result = json.load(res)
        assert not result['videos']['light']['available']
        assert not result['videos']['dark']['available']
        assert not result['demo']
        assert res.headers['Cache-Control'] == 'no-store'
    with pytest.raises(HTTPError) as err:
        get(url + '/videos/light')
    assert err.value.code == 404


def test_manifest_and_partial_video_requests(site):
    data, url = site
    payload = b'0123456789abcdef'
    (data / 'video' / 'newscard_light.mp4').write_bytes(payload)
    with get(url + '/api/videos') as res:
        result = json.load(res)
    video = result['videos']['light']
    assert video['available'] and video['url'].startswith('/videos/light?v=')
    assert not result['videos']['dark']['available']
    with get(url + video['url'], headers={'Range': 'bytes=2-5'}) as res:
        assert res.status == 206
        assert res.headers['Content-Range'] == 'bytes 2-5/16'
        assert res.headers['Accept-Ranges'] == 'bytes'
        assert res.headers['Content-Type'] == 'video/mp4'
        assert res.read() == b'2345'
    with get(url + video['url'], headers={'Range': 'bytes=-3'}) as res:
        assert res.read() == b'def'
    with get(url + video['url'], method='HEAD') as res:
        assert res.status == 200 and res.headers['Content-Length'] == '16'
        assert res.read() == b''
    with pytest.raises(HTTPError) as err:
        get(url + video['url'], headers={'Range': 'bytes=20-30'})
    assert err.value.code == 416 and err.value.headers['Content-Range'] == 'bytes */16'


def test_only_explicit_paths_are_served(site):
    data, url = site
    assert '브리핑' in get(url + '/').read().decode('utf-8')
    for path in ['/data/top_events.json', '/videos/../top_events.json', '/api/../data/top_events.json', '/.env']:
        with pytest.raises(HTTPError) as err:
            get(url + path)
        assert err.value.code == 404
    # A symlink at a permitted name must not expose any file outside data/video.
    try:
        (data / 'video' / 'newscard_dark.mp4').symlink_to(data / 'top_events.json')
    except (OSError, NotImplementedError):
        return  # Windows may not permit symlinks for this user.
    with pytest.raises(HTTPError) as err:
        get(url + '/videos/dark')
    assert err.value.code == 404


@pytest.mark.parametrize('header,size,expected', [
    ('bytes=0-0', 5, (0, 0)), ('bytes=2-', 5, (2, 4)),
    ('bytes=-9', 5, (0, 4)), ('bytes=-0', 5, None),
    ('bytes=5-', 5, None), ('bytes=3-2', 5, None),
    ('bytes=0-1,3-4', 5, None), ('bytes=-', 5, None),
    ('bytes=0-1', 0, None),
])
def test_parse_range(header, size, expected):
    assert parse_range(header, size) == expected
