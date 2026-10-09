import math
import shutil
import unittest

from tests import test_simulation_batches as fixture


LOOP_DECK = '''Two-pole declared negative-feedback loop gain L(s)
VIN in 0 AC 1
EGAIN drive 0 in 0 100
R1 drive pole1 1k
C1 pole1 0 159.154943091895n
EBUFFER buffer 0 pole1 0 1
R2 buffer out 1k
C2 out 0 15.9154943091895n
.end
'''


def specification():
    return {'analysis':{'type':'ac','parameters':{'start':1,'stop':1e7,'points':200}},
            'measurements':[{'kind':'phase_margin','name':'margin_deg','input':'in','output':'out',
                             'loop_gain_convention':'negative_feedback_L'}]}


class PhaseMarginTests(unittest.TestCase):
    setUp=fixture.BatchTests.setUp
    start=fixture.BatchTests.start

    def test_explicit_loop_gain_convention_and_ac_are_required(self):
        self.workspace.projects.write(self.pid,'testbenches/pre.cir',LOOP_DECK)
        spec=specification();del spec['measurements'][0]['loop_gain_convention']
        with self.assertRaisesRegex(ValueError,'loop.gain.convention'):
            self.prepare(self.workspace,self.pid,spec)
        spec=specification();spec['analysis']={'type':'tran'}
        with self.assertRaisesRegex(ValueError,'AC'):
            self.prepare(self.workspace,self.pid,spec)
        self.assertEqual(self.workspace.runs(self.pid),[])

    @unittest.skipUnless(shutil.which('ngspice'),'real ngspice required')
    def test_real_two_pole_loop_matches_analytic_margin_and_records_unity_evidence(self):
        self.workspace.projects.write(self.pid,'testbenches/pre.cir',LOOP_DECK)
        result=self.start(specification());self.assertEqual(result['status'],'succeeded',result)
        measured=result['iterations'][0]['metrics']['margin_deg']
        # (1 + f²/f1²)(1 + f²/f2²) = DC_gain².
        f1,f2,gain=1000.,10000.,100.
        b=f1*f1+f2*f2
        f=math.sqrt((-b+math.sqrt(b*b+4*(gain*gain-1)*f1*f1*f2*f2))/2)
        expected=180-math.degrees(math.atan(f/f1)+math.atan(f/f2))
        self.assertAlmostEqual(measured,expected,delta=.02)
        run=self.workspace.runs(self.pid)[0]
        definition=run['metadata']['conditions']['measurement_definitions']['margin_deg']
        self.assertEqual(definition['loop_gain_convention'],'negative_feedback_L')
        self.assertAlmostEqual(run['metrics'][definition['unity_frequency_metric']],f,delta=3)
        self.assertIn('180',definition['definition'])

    @unittest.skipUnless(shutil.which('ngspice'),'real ngspice required')
    def test_missing_crossing_low_gain_and_wrong_polarity_never_become_margin(self):
        for source,stop in ((LOOP_DECK,1000),
                            (LOOP_DECK.replace('in 0 100','in 0 0.5'),1e7),
                            (LOOP_DECK.replace('in 0 100','in 0 -100'),1e7)):
            with self.subTest(stop=stop,source=source.splitlines()[2]):
                self.workspace.projects.write(self.pid,'testbenches/pre.cir',source)
                spec=specification();spec['analysis']['parameters']['stop']=stop
                result=self.start(spec)
                self.assertEqual(result['status'],'failed',result)
                self.assertNotIn('margin_deg',result['iterations'][0]['metrics'])
                self.assertTrue(result['iterations'][0]['run_id'])

    @unittest.skipUnless(shutil.which('ngspice'),'real ngspice required')
    def test_three_pole_loop_retains_negative_margin_after_phase_unwrap(self):
        source=LOOP_DECK.replace('.end',
            'EBUFFER2 buffer2 0 out 0 1\nR3 buffer2 final 1k\n'
            'C3 final 0 15.9154943091895n\n.end')
        self.workspace.projects.write(self.pid,'testbenches/pre.cir',source)
        spec=specification();spec['measurements'][0]['output']='final'
        result=self.start(spec);self.assertEqual(result['status'],'succeeded',result)
        # Independently solve the unity magnitude of three cascaded real poles.
        poles=(1000.,10000.,10000.);low,high=1.,1e7
        for _ in range(100):
            midpoint=(low+high)/2
            magnitude=100/math.prod(math.hypot(1,midpoint/pole) for pole in poles)
            if magnitude>1:low=midpoint
            else:high=midpoint
        expected=180-sum(math.degrees(math.atan(high/pole)) for pole in poles)
        self.assertLess(expected,0)
        self.assertAlmostEqual(result['iterations'][0]['metrics']['margin_deg'],expected,delta=.02)


if __name__=='__main__':unittest.main()
