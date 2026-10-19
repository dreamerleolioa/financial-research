from argparse import Namespace
import json
from unittest.mock import Mock

import pytest

from test_gcp_cutover import load
from test_gcp_production import service, IMAGE


def arguments(tmp_path, **updates):
    values=dict(access='public',maintenance='true',expected_image=IMAGE,journal=tmp_path/'journal.json')
    values.update(updates)
    return Namespace(**values)


def test_entry_plan_and_missing_approval_never_calls_cloud(tmp_path,monkeypatch,capsys):
    m=load('configure-production-entry'); cloud=Mock();monkeypatch.setattr(m,'cloud',cloud)
    m.main(['--access','public','--expected-image',IMAGE])
    assert json.loads(capsys.readouterr().out)['mode']=='plan_only'
    monkeypatch.delenv('CONFIRM_PRODUCTION_ENTRY_CHANGE',raising=False)
    with pytest.raises(ValueError,match='approval'):m.apply(arguments(tmp_path))
    cloud.assert_not_called()


def test_release_requires_separate_cutover_approval_before_any_cloud_call(tmp_path,monkeypatch):
    m=load('configure-production-entry');cloud=Mock();monkeypatch.setattr(m,'cloud',cloud)
    monkeypatch.setenv('CONFIRM_PRODUCTION_ENTRY_CHANGE','yes')
    monkeypatch.delenv('CONFIRM_PRODUCTION_CUTOVER_APPROVED',raising=False)
    with pytest.raises(ValueError,match='formal cutover'):m.apply(arguments(tmp_path,maintenance='false'))
    cloud.assert_not_called()


def test_old_image_cannot_be_made_public_without_real_maintenance_probe(tmp_path,monkeypatch):
    m=load('configure-production-entry');calls=[];data=service()
    monkeypatch.setenv('CONFIRM_PRODUCTION_ENTRY_CHANGE','yes')
    monkeypatch.setattr(m,'describe',lambda:(data,{'bindings':[]}))
    monkeypatch.setattr(m,'cloud',lambda *args,**kwargs:calls.append(args) or 'fixture-token')
    monkeypatch.setattr(m,'probe_maintenance',Mock(side_effect=ValueError('old image')))
    with pytest.raises(ValueError,match='old image'):m.apply(arguments(tmp_path))
    assert not any('--no-invoker-iam-check' in args for args in calls)
    assert json.loads((tmp_path/'journal.json').read_text())['status']=='stopped_no_automatic_retry'
    before=len(calls)
    with pytest.raises(FileExistsError):m.apply(arguments(tmp_path))
    assert len(calls)==before
