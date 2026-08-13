"""Paid-run authorization, token ledger, and circuit-breaker primitives."""
from __future__ import annotations
import hashlib, json, subprocess, uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import os


def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def git_head(root: Path) -> str:
    return subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()

def utc_now() -> str: return datetime.now(timezone.utc).isoformat()

@dataclass
class TokenAttempt:
    attempt_id:str; qid:str; provider:str; model:str; stage:str
    prompt_tokens:int=0; completion_tokens:int=0; total_tokens:int=0
    status:str='STARTED'; started_at:str=''; completed_at:str=''
    error_at:str=''; timeout_at:str=''; final_status:str='STARTED'
    provider_request_id:str=''
    pre_call_block_reason:str=''
    failure_category:str=''
    error_type:str=''
    http_status:int|None=None

class TokenLedger:
    def __init__(self,path:Path): self.path=path
    def append(self, attempt:TokenAttempt)->None:
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.path.open('a',encoding='utf-8') as f:
            f.write(json.dumps(asdict(attempt),ensure_ascii=False)+chr(10)); f.flush()
    def rows(self)->list[dict[str,Any]]:
        if not self.path.exists(): return []
        return [json.loads(x) for x in self.path.read_text(encoding='utf-8').splitlines() if x.strip()]
    def _replace_attempt(self, attempt_id:str, updates:dict[str,Any])->dict[str,Any]:
        rows=self.rows(); updated=None
        for row in rows:
            if str(row.get('attempt_id'))==attempt_id:
                row.update(updates); updated=row; break
        if updated is None: raise KeyError(f'unknown attempt_id: {attempt_id}')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix(self.path.suffix+'.tmp')
        tmp.write_text(''.join(json.dumps(row,ensure_ascii=False)+chr(10) for row in rows),encoding='utf-8')
        tmp.replace(self.path)
        return updated
    def begin_attempt(self, *, attempt_id:str, qid:str, provider:str, model:str, stage:str)->dict[str,Any]:
        attempt=TokenAttempt(attempt_id=attempt_id,qid=qid,provider=provider,model=model,stage=stage,
                             status='STARTED',started_at=utc_now(),final_status='STARTED')
        self.append(attempt)
        return asdict(attempt)
    def finalize_attempt(self, attempt_id:str, *, final_status:str, provider_request_id:str='',
                         prompt_tokens:int=0, completion_tokens:int=0, total_tokens:int=0,
                         resolved_model:str='', failure_category:str='', error_type:str='',
                         http_status:int|None=None)->dict[str,Any]:
        status=str(final_status).upper(); now=utc_now()
        updates={'status':status,'final_status':status,'provider_request_id':provider_request_id,
                 'prompt_tokens':int(prompt_tokens or 0),'completion_tokens':int(completion_tokens or 0),
                 'total_tokens':int(total_tokens or 0),
                 'failure_category':str(failure_category or ''),'error_type':str(error_type or ''),
                 'http_status':None if http_status is None else int(http_status)}
        if resolved_model: updates['model']=resolved_model
        if status=='COMPLETED': updates['completed_at']=now
        elif status=='TIMEOUT': updates['timeout_at']=now
        else: updates['error_at']=now
        return self._replace_attempt(attempt_id,updates)
    def used_tokens(self)->int: return sum(int(x.get('total_tokens',0) or 0) for x in self.rows())
    def processed_qids(self)->set[str]:
        terminal={'COMPLETED','ERROR','TIMEOUT','PRE_CALL_BLOCKED','PRE_CALL_BLOCKED_ACKNOWLEDGED'}
        return {str(x.get('qid')) for x in self.rows() if x.get('qid') and str(x.get('final_status') or x.get('status')).upper() in terminal}
    def inflight_attempts(self)->list[dict[str,Any]]:
        terminal={'COMPLETED','ERROR','TIMEOUT','PRE_CALL_BLOCKED','PRE_CALL_BLOCKED_ACKNOWLEDGED'}
        return [row for row in self.rows() if str(row.get('final_status') or row.get('status')).upper() not in terminal]
    def blocked_qids(self)->set[str]:
        return {str(row.get('qid')) for row in self.inflight_attempts() if row.get('qid')}

@dataclass(frozen=True)
class PaidRunPolicy:
    max_questions:int; token_budget:int; per_question_token_budget:int
    approval_level:str; allowed_qids:tuple[str,...]
    failure_policy:dict[str,Any]; circuit_breaker_policy:dict[str,Any]

def validate_paid_run(*,allow_paid_run:bool,max_questions:int|None,token_budget:int|None,
                      per_question_token_budget:int|None,manifest_path:Path|None,
                      root:Path,config_path:Path,question_set_path:Path|None=None)->tuple[dict[str,Any],PaidRunPolicy]:
    missing=[]
    if not allow_paid_run: missing.append('--allow-paid-run')
    if not max_questions or max_questions<=0: missing.append('--max-questions N')
    if not token_budget or token_budget<=0: missing.append('--token-budget N')
    if not per_question_token_budget or per_question_token_budget<=0: missing.append('--per-question-token-budget N')
    if manifest_path is None: missing.append('--run-manifest PATH')
    if missing: raise SystemExit('Paid API run refused; required explicit authorization: '+', '.join(missing))
    data=json.loads(manifest_path.read_text(encoding='utf-8'))
    checks={'approved_commit':git_head(root),'config_sha256':sha256_file(config_path)}
    if question_set_path and question_set_path.exists(): checks['question_set_sha256']=sha256_file(question_set_path)
    for key,actual in checks.items():
        expected=str(data.get(key) or '')
        if not expected or expected!=actual: raise SystemExit(f'Run manifest mismatch: {key} expected={expected!r} actual={actual!r}')
    if int(data.get('max_questions',0))!=max_questions: raise SystemExit('Run manifest mismatch: max_questions')
    if int(data.get('token_budget',0))!=token_budget: raise SystemExit('Run manifest mismatch: token_budget')
    if int(data.get('per_question_token_budget',0))!=per_question_token_budget: raise SystemExit('Run manifest mismatch: per_question_token_budget')
    allowed=tuple(str(x) for x in data.get('allowed_qids',[]))
    return data,PaidRunPolicy(max_questions,token_budget,per_question_token_budget,str(data.get('approval_level','')),allowed,dict(data.get('failure_policy') or {}),dict(data.get('circuit_breaker_policy') or {}))

def write_circuit_breaker(path:Path,*,reason:str,processed_qids:Iterable[str],used_tokens:int,last_attempt_id:str='')->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    payload={'triggered':True,'reason':reason,'processed_qids':list(processed_qids),'used_tokens':used_tokens,'last_attempt_id':last_attempt_id,'created_at':utc_now()}
    tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(path)

class CircuitBreaker:
    """Deterministic paid-run circuit breaker for canary and stable batches."""
    def __init__(self, mode: str = 'canary') -> None:
        if mode not in {'canary', 'stable'}:
            raise ValueError(f'unsupported circuit mode: {mode}')
        self.mode = mode
        self.processed = 0
        self.accepted = 0
        self.reasons: list[str] = []
        self.structural_errors = 0

    def observe(self, *, final_state: str, reason: str = '', structural_error: bool = False,
                token_usage_known: bool = True, per_question_budget_ok: bool = True) -> str | None:
        self.processed += 1
        if str(final_state).upper() == 'ACCEPTED':
            self.accepted += 1
        if reason:
            self.reasons.append(reason)
        if structural_error:
            self.structural_errors += 1
        if not token_usage_known:
            return 'missing_token_usage'
        if not per_question_budget_ok:
            return 'per_question_token_budget_exceeded'
        if self.mode == 'canary':
            if str(final_state).upper() != 'ACCEPTED':
                return 'first_unexpected_block'
            if len(self.reasons) >= 2 and self.reasons[-1] == self.reasons[-2]:
                return 'two_same_reason_canary'
            if self.structural_errors >= 2:
                return 'two_structural_errors_canary'
            return None
        if self.processed <= 5 and self.reasons.count(reason) >= 4 and reason:
            return 'four_of_first_five_same_reason'
        if len(self.reasons) >= 5 and len(set(self.reasons[-5:])) == 1:
            return 'five_consecutive_same_reason'
        if self.processed >= 10 and self.accepted == 0:
            return 'zero_accepted_in_first_ten'
        if self.processed >= 10 and (self.processed - self.accepted) / self.processed > 0.70:
            return 'failure_rate_over_70_percent'
        return None

_CURRENT_QID = ""
_CURRENT_STAGE = "llm_chat"

def set_attempt_context(qid: str, stage: str = "llm_chat") -> None:
    global _CURRENT_QID, _CURRENT_STAGE
    _CURRENT_QID = str(qid)
    _CURRENT_STAGE = str(stage)

def current_attempt_context() -> tuple[str, str]:
    return _CURRENT_QID, _CURRENT_STAGE


class ProviderCallBudgetExceeded(RuntimeError):
    """Raised before a provider request when the resolved call budget is exhausted."""

    def __init__(self, qid: str, budget: int, observed_calls: int, scope: str = "per_qid") -> None:
        self.qid = str(qid)
        self.budget = int(budget)
        self.observed_calls = int(observed_calls)
        self.scope = str(scope)
        super().__init__(
            f"provider call budget exceeded before request: scope={self.scope} "
            f"qid={self.qid} observed={self.observed_calls} budget={self.budget}"
        )


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _provider_call_budgets_from_env() -> tuple[dict[str, int], int]:
    raw = os.getenv("SAFE_RUN_PROVIDER_CALL_BUDGETS_JSON", "").strip()
    budgets: dict[str, int] = {}
    if raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                budgets = {str(k): max(0, _safe_int(v, 0)) for k, v in payload.items()}
        except json.JSONDecodeError:
            budgets = {}
    max_total = max(0, _safe_int(os.getenv("SAFE_RUN_MAX_PROVIDER_CALL_BUDGET", ""), 0))
    return budgets, max_total


def _provider_attempt_count(rows: list[dict[str, Any]], qid: str | None = None) -> int:
    ignored = {"PRE_CALL_BLOCKED", "PRE_CALL_BLOCKED_ACKNOWLEDGED"}
    count = 0
    for row in rows:
        if qid is not None and str(row.get("qid")) != str(qid):
            continue
        status = str(row.get("final_status") or row.get("status") or "").upper()
        if status not in ignored:
            count += 1
    return count


def assert_provider_call_allowed(*, path: Path, attempt_id: str, provider: str, model: str) -> None:
    """Pre-call guard for safe paid runs.

    The check runs before TokenLedger.begin_attempt and before any HTTP request.
    When a call is blocked, a zero-token PRE_CALL_BLOCKED row is written for
    observability, but it is not counted as a provider call.
    """
    qid, stage = current_attempt_context()
    qid = qid or "unknown"
    budgets, max_total = _provider_call_budgets_from_env()
    if not budgets and not max_total:
        return
    ledger = TokenLedger(path)
    rows = ledger.rows()
    per_qid_budget = budgets.get(str(qid))
    qid_count = _provider_attempt_count(rows, str(qid))
    if per_qid_budget is not None and qid_count >= per_qid_budget:
        ledger.append(TokenAttempt(
            attempt_id=attempt_id, qid=str(qid), provider=provider, model=model,
            stage=stage, status="PRE_CALL_BLOCKED", final_status="PRE_CALL_BLOCKED",
            started_at=utc_now(), error_at=utc_now(),
        ))
        raise ProviderCallBudgetExceeded(str(qid), per_qid_budget, qid_count, "per_qid")
    total_count = _provider_attempt_count(rows)
    if max_total and total_count >= max_total:
        ledger.append(TokenAttempt(
            attempt_id=attempt_id, qid=str(qid), provider=provider, model=model,
            stage=stage, status="PRE_CALL_BLOCKED", final_status="PRE_CALL_BLOCKED",
            started_at=utc_now(), error_at=utc_now(),
        ))
        raise ProviderCallBudgetExceeded(str(qid), max_total, total_count, "total")

def record_pre_call_blocked(*, reason: str, model: str = "", stage: str = "prompt_budget_gate") -> str:
    """Persist a zero-token pre-provider block for paid-run auditability.

    The helper is inert outside an explicitly safe paid run.  It records no
    provider call and never creates a STARTED attempt.
    """
    raw_path = os.getenv("LLM_TOKEN_LEDGER_PATH", "").strip()
    if not raw_path or os.getenv("SAFE_RUN_EXECUTION", "").strip() != "1":
        return ""
    qid, current_stage_name = current_attempt_context()
    attempt_id = f"precall-{uuid.uuid4().hex[:12]}"
    now = utc_now()
    TokenLedger(Path(raw_path)).append(TokenAttempt(
        attempt_id=attempt_id,
        qid=qid or "unknown",
        provider="pre_call_gate",
        model=str(model or os.getenv("FREETOKEN_MODEL") or os.getenv("LLM_MODEL_ID") or ""),
        stage=str(stage or current_stage_name or "prompt_budget_gate"),
        status="PRE_CALL_BLOCKED",
        started_at=now,
        error_at=now,
        final_status="PRE_CALL_BLOCKED",
        pre_call_block_reason=str(reason or "pre_call_blocked"),
    ))
    return attempt_id


def begin_provider_attempt(*, path: Path, attempt_id: str, provider: str, model: str) -> None:
    assert_provider_call_allowed(path=path, attempt_id=attempt_id, provider=provider, model=model)
    qid, stage = current_attempt_context()
    TokenLedger(path).begin_attempt(
        attempt_id=attempt_id, qid=qid or "unknown", provider=provider,
        model=model, stage=stage,
    )


def finalize_provider_attempt(*, path: Path, attempt_id: str, final_status: str,
                              provider_request_id: str = "", resolved_model: str = "",
                              prompt_tokens: int = 0, completion_tokens: int = 0,
                              total_tokens: int = 0, failure_category: str = "",
                              error_type: str = "", http_status: int | None = None) -> None:
    TokenLedger(path).finalize_attempt(
        attempt_id, final_status=final_status, provider_request_id=provider_request_id,
        resolved_model=resolved_model, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, total_tokens=total_tokens,
        failure_category=failure_category, error_type=error_type, http_status=http_status,
    )
