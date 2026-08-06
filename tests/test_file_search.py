from pathlib import Path
from threading import Event

from windows_pet.file_search_models import SearchRequest, SearchRoot
from windows_pet.file_search_service import FileSearchService
from windows_pet.file_search_settings import SearchSettings

def test_fake_responses_function_call_round_trip(monkeypatch, tmp_path: Path):
    (tmp_path / 'report.xlsx').write_text('private body')
    settings = SearchSettings([SearchRoot('r', 'root', str(tmp_path))])

    class Call:
        type = 'function_call'
        call_id = 'call-1'
        name = 'search_files'
        arguments = '{"query":"report","root_ids":["r"],"extensions":[".xlsx"],"modified_after":null,"modified_before":null,"min_size_bytes":null,"max_size_bytes":null,"include_directories":false,"max_results":50}'

    class Response:
        def __init__(self, output, output_text=''):
            self.output = output
            self.output_text = output_text

    class Responses:
        def __init__(self): self.calls = []
        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1: return Response([Call()])
            return Response([], '1 file found')

    class FakeClient:
        def __init__(self): self.responses = Responses()

    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setattr('windows_pet.ai_client.ToolDispatcher', lambda: ToolDispatcherForTest(settings))
    from windows_pet.ai_client import AIClient
    fake = FakeClient(); completed = []
    text = AIClient(fake).stream_with_tools([{'role':'user','content':'find report'}], lambda value: None, on_search_completed=completed.append)

    assert text == '1 file found'
    assert len(fake.responses.calls) == 2
    output = fake.responses.calls[1]['input'][-1]
    assert output['call_id'] == 'call-1'
    assert 'full_path' not in output['output']
    assert str(tmp_path) not in output['output']
    assert completed[0]['results'][0]['full_path'] == str(tmp_path / 'report.xlsx')

class ToolDispatcherForTest:
    def __init__(self, settings): self.service = FileSearchService(settings)
    def search_files(self, arguments, cancel=None):
        import json
        data = json.loads(arguments)
        result = self.service.search(SearchRequest(data['query'], tuple(data['root_ids']), tuple(data['extensions'])))
        result['results'] = [row.__dict__ | {'modified_at': row.modified_at.isoformat()} for row in result['results']]
        return result
    @staticmethod
    def safe_output(result):
        return {'status':'success', 'search_id':None, 'total_found':result['total_found'], 'returned_to_model':len(result['results']), 'truncated':False, 'elapsed_ms':0, 'results':[{'result_id':r['result_id'],'name':r['name'],'extension':r['extension'],'root_alias':r['root_alias'],'modified_at':r['modified_at'],'size_bytes':r['size_bytes']} for r in result['results']]}

def test_search_is_allowlisted_and_metadata_only(tmp_path: Path):
    (tmp_path / 'old').mkdir(); (tmp_path / '2026年度_PC予算.xlsx').write_text('secret')
    (tmp_path / 'old' / 'note.txt').write_text('x')
    settings = SearchSettings([SearchRoot('r', '共有', str(tmp_path))])
    result = FileSearchService(settings).search(SearchRequest('PC予算', ('r',), ('.xlsx',)))
    assert [x.name for x in result['results']] == ['2026年度_PC予算.xlsx']
    assert result['results'][0].full_path == str(tmp_path / '2026年度_PC予算.xlsx')

def test_unknown_root_and_symlink_are_rejected(tmp_path: Path):
    (tmp_path / 'x.txt').write_text('x')
    service = FileSearchService(SearchSettings([SearchRoot('r', 'root', str(tmp_path))]))
    try: service.search(SearchRequest('', ('missing',)))
    except ValueError: pass
    else: assert False
    (tmp_path / 'link.txt').symlink_to(tmp_path / 'x.txt')
    assert [x.name for x in service.search(SearchRequest('')).get('results', [])] == ['x.txt']

def test_cancel_stops_search(tmp_path: Path):
    (tmp_path / 'x.txt').write_text('x')
    event = Event(); event.set()
    result = FileSearchService(SearchSettings([SearchRoot('r','root',str(tmp_path))])).search(SearchRequest(''), event)
    assert result['status'] == 'cancelled'
