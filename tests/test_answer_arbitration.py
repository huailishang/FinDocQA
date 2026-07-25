from contracts import Question
from verification.answer_arbitration import canonical_answer,duplicate_option_decision,_parse_final_answer

def test_multi_answer_is_canonicalized(): assert canonical_answer('CBAA','multi')=='ABC'
def test_duplicate_options_choose_first_matching_letter():
    q=Question('case_015','insurance','x',{'A':'same','B':'same','C':'same','D':'other'},'mcq',[])
    d=duplicate_option_decision(q,'C'); assert d and d.answer=='A' and d.source=='deterministic_duplicate_mapping'
def test_parse_final_answer(): assert _parse_final_answer('最终答案：ABC','multi')=='ABC'
