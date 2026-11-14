# coding: utf-8
import json

import pytest
import requests

from uav_downloader.subtitles import llm
from uav_downloader.core.ssl import SharedSSLAdapter


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None,
                 json_error=None):
        self.status_code = status_code
        self.payload = payload
        self.headers = headers or {}
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class StreamingResponse(FakeResponse):
    def __init__(self, chunks, **kwargs):
        super().__init__(**kwargs)
        self.chunks = list(chunks)
        self.closed = False

    def iter_content(self, **_kwargs):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.mounts = []
        self.calls = []
        self.closed = False

    def mount(self, prefix, adapter):
        self.mounts.append((prefix, adapter))

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


def _settings(provider='openai', **kwargs):
    values = {
        'enabled': True,
        'provider': provider,
        'model': 'test-model',
        'retry_backoff_seconds': 0,
    }
    values.update(kwargs)
    return llm.LlmTranslationSettings(**values)


def _openai_response(items):
    return FakeResponse(payload={
        'choices': [{
            'finish_reason': 'stop',
            'message': {'content': json.dumps(
                items, ensure_ascii=False)},
        }],
    })


def _anthropic_response(items):
    return FakeResponse(payload={
        'stop_reason': 'end_turn',
        'content': [{'type': 'text', 'text': json.dumps(
            items, ensure_ascii=False)}],
    })


def _gemini_response(items):
    return FakeResponse(payload={
        'candidates': [{
            'finishReason': 'STOP',
            'content': {'parts': [{'text': json.dumps(
                items, ensure_ascii=False)}]},
        }],
    })


def test_external_translation_is_disabled_by_default(monkeypatch):
    def unexpected_session():
        pytest.fail('disabled translation must not create a session')

    monkeypatch.setattr(llm.requests, 'Session', unexpected_session)
    with pytest.raises(llm.LlmTranslationError, match='disabled'):
        llm.translate_cues(
            ['こんにちは'], llm.LlmTranslationSettings(), api_key='secret')


@pytest.mark.parametrize('provider', sorted(llm.SUPPORTED_PROVIDERS))
def test_settings_accept_all_supported_providers(provider):
    settings = _settings(provider.upper())
    assert settings.provider == provider
    assert 'api_key' not in settings.__dataclass_fields__


@pytest.mark.parametrize('changes', [
    {'provider': 'unknown'},
    {'batch_size': 0},
    {'max_retries': 6},
    {'timeout_seconds': 0},
    {'max_output_tokens': 100},
])
def test_settings_reject_invalid_values(changes):
    with pytest.raises(ValueError):
        _settings(**changes)


def test_openai_batches_in_order_mounts_shared_ssl_and_reports_progress():
    session = FakeSession([
        _openai_response([
            {'id': 0, 'text': '不，請停下。'},
            {'id': 1, 'text': '我同意。'},
        ]),
        _openai_response([
            {'id': 2, 'text': '我無法呼吸。'},
        ]),
    ])
    progress = []
    result = llm.translate_cues(
        ['いや、止めて。', '同意します。', '息ができない。'],
        _settings(batch_size=2),
        api_key='top-secret',
        session=session,
        progress_callback=lambda complete, total: progress.append(
            (complete, total)),
    )

    assert result.translations == (
        '不，請停下。', '我同意。', '我無法呼吸。')
    assert result.cue_count == 3
    assert result.batch_count == 2
    assert progress == [(2, 3), (3, 3)]
    assert len(session.calls) == 2
    assert session.mounts[0][0] == 'https://'
    assert isinstance(session.mounts[0][1], SharedSSLAdapter)
    assert session.calls[0][0] == (
        'https://api.openai.com/v1/chat/completions')
    first_request = session.calls[0][1]
    assert first_request['headers']['Authorization'] == 'Bearer top-secret'
    assert first_request['timeout'] == (10.0, 90.0)
    assert first_request['allow_redirects'] is False
    assert first_request['stream'] is True
    assert first_request['json']['max_completion_tokens'] == 8192
    schema = first_request['json']['response_format']['json_schema']
    assert schema['strict'] is True
    assert schema['schema']['type'] == 'array'
    prompt = first_request['json']['messages'][0]['content']
    assert 'do not censor' in prompt
    assert 'consent, refusal, negation' in prompt
    assert 'medical' in prompt


def test_provider_requests_follow_the_app_proxy_setting(monkeypatch):
    from uav_downloader.core import config

    routes = {
        'http': 'http://127.0.0.1:8080',
        'https': 'http://127.0.0.1:8080',
    }
    monkeypatch.setattr(
        config, 'proxy_request_kwargs', lambda: {'proxies': routes})
    session = FakeSession([
        _openai_response([{'id': 0, 'text': 'Hello'}]),
    ])

    llm.translate_cues(
        ['こんにちは'], _settings('openai'),
        api_key='secret', session=session)

    assert session.calls[0][1]['proxies'] == routes


def test_proxy_configuration_failure_falls_back_to_explicit_direct(
        monkeypatch):
    from uav_downloader.core import config

    def fail_proxy_read():
        raise OSError("proxy settings unavailable")

    monkeypatch.setattr(config, 'proxy_request_kwargs', fail_proxy_read)
    session = FakeSession([
        _openai_response([{'id': 0, 'text': 'Hello'}]),
    ])

    llm.translate_cues(
        ['こんにちは'], _settings('openai'),
        api_key='secret', session=session)

    assert session.calls[0][1]['proxies'] == {
        'http': '', 'https': '', 'all': ''}


def test_loopback_provider_bypasses_the_app_proxy(monkeypatch):
    from uav_downloader.core import config

    monkeypatch.setattr(
        config,
        'proxy_request_kwargs',
        lambda: pytest.fail('loopback must never consult the app proxy'))
    session = FakeSession([
        _openai_response([{'id': 0, 'text': 'Local output'}]),
    ])

    llm.translate_cues(
        ['入力'],
        _settings(
            'openai-compatible',
            base_url='http://127.0.0.1:11434/v1'),
        api_key='local-secret',
        session=session,
    )

    assert session.calls[0][1]['proxies'] == {
        'http': '', 'https': '', 'all': ''}
    assert session.calls[0][1]['allow_redirects'] is False


def test_owned_session_is_closed(monkeypatch):
    session = FakeSession([
        _openai_response([{'id': 0, 'text': 'Hello.'}]),
    ])
    monkeypatch.setattr(llm.requests, 'Session', lambda: session)

    llm.translate_cues(
        ['こんにちは'], _settings(), api_key='secret')

    assert session.closed


def test_anthropic_request_and_response_shape():
    session = FakeSession([
        _anthropic_response([{'id': 0, 'text': 'Stop.'}]),
    ])
    result = llm.translate_cues(
        ['止めて。'], _settings('anthropic'), api_key='anthropic-secret',
        target_language='English', session=session)

    url, request = session.calls[0]
    assert url == 'https://api.anthropic.com/v1/messages'
    assert request['headers']['x-api-key'] == 'anthropic-secret'
    assert request['headers']['anthropic-version'] == '2023-06-01'
    assert request['json']['system'].startswith(
        'You are a professional subtitle translator.')
    assert result.translations == ('Stop.',)


def test_gemini_keeps_api_key_out_of_url():
    session = FakeSession([
        _gemini_response([{'id': 0, 'text': '請叫醫生。'}]),
    ])
    result = llm.translate_cues(
        ['医者を呼んで。'], _settings('gemini'), api_key='gemini-secret',
        session=session)

    url, request = session.calls[0]
    assert url.endswith('/models/test-model:generateContent')
    assert 'gemini-secret' not in url
    assert request['headers']['x-goog-api-key'] == 'gemini-secret'
    assert request['json']['systemInstruction']['parts'][0]['text'].startswith(
        'You are a professional subtitle translator.')
    assert 'You are a professional subtitle translator.' not in (
        request['json']['contents'][0]['parts'][0]['text'])
    assert request['json']['generationConfig']['responseMimeType'] == (
        'application/json')
    assert request['json']['generationConfig']['responseSchema']['type'] == (
        'array')
    assert result.translations == ('請叫醫生。',)


def test_keyless_openai_compatible_endpoint_is_supported():
    session = FakeSession([
        _openai_response([{'id': 0, 'text': 'Local output'}]),
    ])
    result = llm.translate_cues(
        ['入力'],
        _settings(
            'openai-compatible',
            base_url='http://127.0.0.1:11434/v1/'),
        session=session,
    )

    url, request = session.calls[0]
    assert url == 'http://127.0.0.1:11434/v1/chat/completions'
    assert 'Authorization' not in request['headers']
    assert request['json']['max_tokens'] == 8192
    assert 'response_format' not in request['json']
    assert result.provider == 'openai-compatible'


def test_provider_url_rejects_query_credentials():
    with pytest.raises(llm.LlmTranslationError, match='URL is invalid'):
        llm.translate_cues(
            ['入力'],
            _settings(
                'openai-compatible',
                base_url='https://example.invalid/v1?key=secret'),
            api_key='secret',
            session=FakeSession(),
        )


def test_redirect_response_is_rejected_without_following_it():
    session = FakeSession([
        FakeResponse(
            status_code=302,
            headers={'Location': 'https://attacker.invalid/collect'}),
    ])
    with pytest.raises(
            llm.LlmTranslationError,
            match=r'rejected the request \(HTTP 302\)'):
        llm.translate_cues(
            ['入力'], _settings('anthropic'),
            api_key='secret', session=session)
    assert session.calls[0][1]['allow_redirects'] is False


def test_provider_url_rejects_remote_plain_http():
    with pytest.raises(
            llm.LlmTranslationError,
            match='HTTPS except on this computer'):
        llm.translate_cues(
            ['入力'],
            _settings(
                'openai-compatible',
                base_url='http://example.invalid/v1'),
            api_key='secret',
            session=FakeSession(),
        )


@pytest.mark.parametrize(
    ('provider', 'base_url'),
    [
        ('openai', 'https://attacker.invalid/v1'),
        ('anthropic', 'https://attacker.invalid/v1'),
        ('gemini', 'https://attacker.invalid/v1beta'),
    ],
)
def test_request_layer_rejects_custom_hosts_for_official_providers(
        provider, base_url):
    with pytest.raises(
            llm.LlmTranslationError,
            match='official API URL'):
        llm.translate_cues(
            ['入力'],
            _settings(provider, base_url=base_url),
            api_key='secret',
            session=FakeSession(),
        )


@pytest.mark.parametrize('content', [
    '```json\n[{"id":0,"text":"Hello"}]\n```',
    '{"translations":[{"id":0,"text":"Hello"}]}',
    '[{"id":1,"text":"Hello"}]',
    '[{"id":0,"text":"Hello","note":"extra"}]',
    '[{"id":0,"text":""}]',
    '[{"id":0,"id":0,"text":"Hello"}]',
])
def test_strict_json_array_contract_rejects_invalid_content(content):
    session = FakeSession([FakeResponse(payload={
        'choices': [{'message': {'content': content}}],
    })])
    with pytest.raises(llm.LlmTranslationError):
        llm.translate_cues(
            ['こんにちは'], _settings(), api_key='secret', session=session)


@pytest.mark.parametrize(
    ('provider', 'payload'),
    [
        (
            'openai',
            {
                'choices': [{
                    'finish_reason': 'tool_calls',
                    'message': {'content': '[{"id":0,"text":"unsafe"}]'},
                }],
            },
        ),
        (
            'openai',
            {
                'choices': [{
                    'message': {'content': '[{"id":0,"text":"unsafe"}]'},
                }],
            },
        ),
        (
            'openai',
            {
                'choices': [{
                    'finish_reason': 'stop',
                    'message': {
                        'content': '[{"id":0,"text":"unsafe"}]',
                        'refusal': 'blocked',
                    },
                }],
            },
        ),
        (
            'anthropic',
            {
                'stop_reason': 'max_tokens',
                'content': [{
                    'type': 'text',
                    'text': '[{"id":0,"text":"unsafe"}]',
                }],
            },
        ),
        (
            'anthropic',
            {
                'content': [{
                    'type': 'text',
                    'text': '[{"id":0,"text":"unsafe"}]',
                }],
            },
        ),
        (
            'gemini',
            {
                'candidates': [{
                    'finishReason': 'SAFETY',
                    'content': {
                        'parts': [{
                            'text': '[{"id":0,"text":"unsafe"}]',
                        }],
                    },
                }],
            },
        ),
        (
            'gemini',
            {
                'candidates': [{
                    'content': {
                        'parts': [{
                            'text': '[{"id":0,"text":"unsafe"}]',
                        }],
                    },
                }],
            },
        ),
    ],
)
def test_incomplete_or_refused_provider_responses_are_never_accepted(
        provider, payload):
    with pytest.raises(
            llm.LlmTranslationError,
            match='invalid response structure'):
        llm.translate_cues(
            ['入力'], _settings(provider),
            api_key='secret',
            session=FakeSession([FakeResponse(payload=payload)]),
        )


def test_compatible_provider_can_omit_finish_reason_for_legacy_servers():
    payload = {
        'choices': [{
            'message': {'content': '[{"id":0,"text":"accepted"}]'},
        }],
    }
    result = llm.translate_cues(
        ['入力'],
        _settings(
            'openai-compatible',
            base_url='http://127.0.0.1:11434/v1'),
        session=FakeSession([FakeResponse(payload=payload)]),
    )
    assert result.translations == ('accepted',)


def test_declared_oversized_provider_response_is_rejected_before_json():
    response = FakeResponse(
        payload={'must': 'not be parsed'},
        headers={'Content-Length': str(llm._MAX_RESPONSE_BYTES + 1)})
    with pytest.raises(
            llm.LlmTranslationError,
            match='oversized response'):
        llm.translate_cues(
            ['入力'], _settings('openai'),
            api_key='secret', session=FakeSession([response]))


def test_streamed_response_checks_cancellation_between_chunks():
    response = StreamingResponse([b'{}'])
    with pytest.raises(llm.LlmTranslationCancelled):
        llm._read_response_json(
            response, 'openai', cancel_check=lambda: True)


def test_streamed_transport_failure_is_retried_and_sanitized():
    interrupted = StreamingResponse([
        requests.exceptions.ChunkedEncodingError(
            'private transport detail'),
    ])
    session = FakeSession([
        interrupted,
        _openai_response([{'id': 0, 'text': 'Recovered'}]),
    ])

    result = llm.translate_cues(
        ['入力'], _settings('openai', max_retries=1),
        api_key='secret', session=session)

    assert result.translations == ('Recovered',)
    assert len(session.calls) == 2
    assert interrupted.closed


def test_streamed_undeclared_oversized_response_is_rejected():
    response = StreamingResponse([
        b'x' * (llm._MAX_RESPONSE_BYTES + 1),
    ])
    with pytest.raises(
            llm.LlmTranslationError,
            match='oversized response'):
        llm._read_response_json(response, 'openai')


def test_429_and_5xx_have_a_finite_retry_limit():
    session = FakeSession([
        FakeResponse(429, headers={'Retry-After': '0'}),
        FakeResponse(503),
        _openai_response([{'id': 0, 'text': 'Recovered'}]),
    ])
    result = llm.translate_cues(
        ['入力'], _settings(max_retries=2), api_key='secret',
        session=session)

    assert result.translations == ('Recovered',)
    assert len(session.calls) == 3


def test_retry_limit_error_hides_key_and_response_body():
    secret = 'never-show-this-key'
    body = 'never-show-this-response'
    session = FakeSession([
        FakeResponse(500, payload={'error': body}),
        FakeResponse(500, payload={'error': body}),
    ])
    with pytest.raises(llm.LlmTranslationError) as caught:
        llm.translate_cues(
            ['入力'], _settings(max_retries=1), api_key=secret,
            session=session)

    message = str(caught.value)
    assert secret not in message
    assert body not in message
    assert len(session.calls) == 2


def test_non_retryable_status_is_not_retried_or_exposed():
    session = FakeSession([
        FakeResponse(400, payload={'error': 'private provider detail'}),
        _openai_response([{'id': 0, 'text': 'must not be used'}]),
    ])
    with pytest.raises(llm.LlmTranslationError) as caught:
        llm.translate_cues(
            ['入力'], _settings(), api_key='secret', session=session)

    assert len(session.calls) == 1
    assert 'private provider detail' not in str(caught.value)


@pytest.mark.parametrize('failure', [
    requests.Timeout('URL with secret'),
    requests.ConnectionError('headers with secret'),
])
def test_network_failure_is_retried_but_sanitized(failure):
    session = FakeSession([failure, failure])
    with pytest.raises(llm.LlmTranslationError) as caught:
        llm.translate_cues(
            ['入力'], _settings(max_retries=1), api_key='secret',
            session=session)

    assert 'URL with secret' not in str(caught.value)
    assert 'headers with secret' not in str(caught.value)
    assert len(session.calls) == 2


def test_cancellation_before_request():
    session = FakeSession([
        _openai_response([{'id': 0, 'text': 'must not be used'}]),
    ])
    with pytest.raises(llm.LlmTranslationCancelled):
        llm.translate_cues(
            ['入力'], _settings(), api_key='secret', session=session,
            cancel_check=lambda: True)
    assert not session.calls


def test_cancellation_between_retry_attempts(monkeypatch):
    cancelled = [False]
    session = FakeSession([FakeResponse(429)])
    def cancel_during_backoff(*args):
        cancelled[0] = True
        llm._check_cancelled(lambda: cancelled[0])
    monkeypatch.setattr(llm, '_retry_wait', cancel_during_backoff)

    with pytest.raises(llm.LlmTranslationCancelled):
        llm.translate_cues(
            ['入力'], _settings(max_retries=2), api_key='secret',
            session=session, cancel_check=lambda: cancelled[0])
    assert len(session.calls) == 1


def test_invalid_success_envelope_does_not_expose_response_data():
    private_data = 'private-provider-response'
    session = FakeSession([
        FakeResponse(payload={'unexpected': private_data}),
    ])
    with pytest.raises(llm.LlmTranslationError) as caught:
        llm.translate_cues(
            ['入力'], _settings(), api_key='secret', session=session)
    assert private_data not in str(caught.value)


def test_empty_input_needs_no_session_or_request(monkeypatch):
    monkeypatch.setattr(
        llm.requests, 'Session',
        lambda: pytest.fail('empty input must not create a session'))
    result = llm.translate_cues(
        [], _settings(), api_key='secret')
    assert result.translations == ()
    assert result.cue_count == 0
    assert result.batch_count == 0
