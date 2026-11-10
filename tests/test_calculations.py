import math
import pytest
from foodmicro.calculations import (Observation,calculate,initial_fraction,total_fraction,round_sig,
 area_estimate,convert_recovery,confirmation_categories,technical_statistics)


def observations(levels,first=.1,volumes=None):
    result=[]
    for i,counts in enumerate(levels):
        if not isinstance(counts,list):counts=[counts]
        for j,count in enumerate(counts):
            state='numeric' if isinstance(count,(int,float)) else count
            result.append(Observation(f'd{i}-p{j}',count if state=='numeric' else None,first*10**(-i),(volumes or [1]*len(levels))[i],state))
    return result


def test_area_estimate_is_qualified():
    estimated = area_estimate(6, 50, .01, 1, False)
    bound = area_estimate(20, 50, .01, 1, True)
    assert (estimated.value, estimated.qualifier) == (30000, 'estimated')
    assert (bound.value, bound.qualifier, bound.log10) == (100000, 'greater_than', None)


def test_prep_nondecimal_volume_and_duplicate():
    first=initial_fraction(10,490)
    assert total_fraction(first,100)==pytest.approx(1/5000)
    r=calculate(observations([[41,45]],.001))
    assert r.value==43000
    assert calculate(observations([[41,45]],.001,[.5])).value==86000
    assert calculate(observations([[18,22]],.1,[.1])).value==pytest.approx(2000)


def test_methods_cannot_silently_switch():
    o=observations([140,32],.01)
    assert calculate(o,'basic_exposure_v1').value is None
    assert calculate(o,'handbook_apha_2026').value==14000
    assert calculate(o,'bam_apc_2026_numeric').value==pytest.approx(172/.011)
    assert calculate(o,'iso7218_2024').value is None


def test_bam2026_primary_examples_and_rounding():
    assert calculate(observations([[144,118],[26,20]],.01),'bam_apc_2026_numeric').value==pytest.approx(14000)
    assert calculate(observations([[27,51]],.01),'bam_apc_2026_numeric').value==3900
    assert calculate(observations([120,23],.01),'bam_apc_2026_numeric').value==13000
    assert calculate(observations([27],.01),'bam_apc_2026_numeric').value==2700
    assert calculate(observations(['TNTC',27],.01),'bam_apc_2026_numeric').value is None
    assert calculate(observations([14],.01),'bam_apc_2026_numeric').qualifier=='estimated'
    assert calculate(observations([15],.01),'bam_apc_2026_numeric').qualifier=='exact'
    assert round_sig(14500,mode='half_even')==14000
    assert round_sig(15500,mode='half_even')==16000
    assert round_sig(24500,mode='half_up')==25000


def test_confirmation_and_historical_arithmetic():
    o=[Observation('a',150,.1,.1,tested=5,confirmed=4),Observation('b',17,.01,.1,tested=5,confirmed=3)]
    r=calculate(o,'iso7218_2007_2013_arithmetic');assert r.value==pytest.approx(130/.011)
    assert r.status=='requires_method_review'
    assert confirmation_categories([(18,5,4),(2,2,0)])==14
    assert confirmation_categories([(20,5,5),(3,3,1)])==21
    assert calculate(o,'bam_apc_2026_numeric').value is None
    with pytest.raises(ValueError):Observation('x',18,.1,1,tested=5,confirmed=6).validate()
    mixed=[Observation('a',50,.1,1,tested=5,confirmed=4),Observation('b',60,.1,1)]
    assert calculate(mixed).value is None


@pytest.mark.parametrize('invalid',[0,-1,float('inf'),float('nan')])
def test_invalid_exposures(invalid):
    with pytest.raises(ValueError):calculate([Observation('a',50,invalid,1)])
    with pytest.raises(ValueError):calculate([Observation('a',50,.1,invalid)])


def test_missing_distinct_from_zero_and_censored_logs():
    assert calculate([Observation('a',None,.1,1,'missing')]).value is None
    r=calculate([Observation('a',0,.1,1,'zero')]);assert r.value==10 and r.qualifier=='less_than' and r.log10 is None
    with pytest.raises(ValueError):calculate([Observation('a',None,.1,1,'numeric')])
    with pytest.raises(ValueError):calculate([Observation('a',2,.1,1,'zero')])
    with pytest.raises(ValueError):calculate([Observation('a',-1,.1,1)])
    with pytest.raises(ValueError):calculate([Observation('a',2.5,.1,1)])


def test_swab_conversion_qualifiers():
    r=calculate(observations([0],.1),'handbook_apha_2026',unit='CFU/mL')
    x=convert_recovery(r,10,50)
    assert x.value==2 and x.qualifier=='less_than' and x.log10 is None and x.unit=='CFU/cm²'


def test_technical_statistics():
    stats=technical_statistics(observations([[41,45]],.001))
    assert stats['sd_log10']==pytest.approx(.0285873775582859)
    assert technical_statistics(observations([[0,0]],.1))['sd_log10'] is None

