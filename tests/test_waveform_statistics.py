import math
import shutil
import tempfile
import unittest
from pathlib import Path

from analog_factory.service import Workspace
from analog_factory import waveforms


class WaveformStatisticsTests(unittest.TestCase):
    def test_statistics_use_every_raw_sample_without_display_selection_bias(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'waveforms.raw';count=30001
            with path.open('w') as stream:
                stream.write(f'Flags: real\nNo. Variables: 2\nNo. Points: {count}\nVariables:\n0 time time\n1 v(out) voltage\nValues:\n')
                for i in range(count):stream.write(f'{i} {i}\n {100 if i==1237 else -20 if i==9211 else 0}\n')
            display=waveforms.parse_raw(path,max_points=100)
            self.assertIn('display_sampling',display)
            stats=waveforms.raw_statistics(path)['statistics'][1]
            self.assertEqual(stats['count'],count)
            self.assertEqual((stats['min'],stats['max'],stats['peak_to_peak']),(-20,100,120))
            self.assertAlmostEqual(stats['mean'],80/count)
            self.assertAlmostEqual(stats['rms'],math.sqrt(10400/count))
            self.assertNotIn('points',waveforms.raw_statistics(path))
            self.assertNotAlmostEqual(sum(row[1] for row in display['points'])/len(display['points']),stats['mean'])

    def test_full_statistics_preserve_complex_magnitude_semantics_and_empty_plot(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'waveforms.raw'
            path.write_text('Flags: complex\nNo. Variables: 2\nNo. Points: 2\nVariables:\n0 frequency frequency\n1 v(out) voltage\nValues:\n0 10,0\n 3,4\n1 100,0\n 0,12\n')
            stats=waveforms.raw_statistics(path)
            self.assertEqual(stats['variables'][1]['component'],'magnitude')
            self.assertEqual(stats['statistics'][1]['mean'],8.5)
            self.assertEqual(stats['statistics'][1]['rms'],math.sqrt(84.5))
            path.write_text('Flags: real\nNo. Variables: 1\nNo. Points: 0\nVariables:\n0 time time\nValues:\n')
            self.assertIsNone(waveforms.raw_statistics(path)['statistics'][0]['mean'])

    def test_large_finite_values_do_not_overflow_mean_or_rms_accumulation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'waveforms.raw'
            path.write_text('Flags: real\nNo. Variables: 2\nNo. Points: 4\nVariables:\n0 time time\n1 v(out) voltage\nValues:\n0 0\n 1e200\n1 1\n -1e200\n2 2\n 1e200\n3 3\n -1e200\n')
            value=waveforms.raw_statistics(path)['statistics'][1]
            self.assertEqual(value['mean'],0)
            self.assertEqual(value['rms'],1e200)

    @unittest.skipUnless(shutil.which('ngspice'),'real ngspice required')
    def test_registered_statistics_and_graph_measure_use_actual_full_rawfile(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory));self.addCleanup(workspace.operations.close)
            pid=workspace.projects.create('Full sample statistics')['id']
            workspace.projects.write(pid,'testbenches/pre.cir','Full raw measurement\nV1 out 0 PULSE(0 1 1m 1u 1u 1u 10m)\nR1 out 0 1k\n.tran 1u 50m\n.end\n')
            flow={'schema_version':2,'nodes':[{'id':'sim','type':'simulate'},
                {'id':'mean','type':'measure','config':{'signal':'v(out)','statistic':'mean'},'inputs':{'run_id':{'node':'sim','port':'run_id'}}}],
                'outputs':{'mean':{'node':'mean','port':'value'}}}
            job=workspace.start_workflow(pid,flow)
            result=workspace.jobs.wait(workspace.projects.path(pid),job['id'],20)
            self.assertEqual(result['status'],'succeeded',result)
            run_id=result['nodes']['sim']['outputs']['run_id']
            parsed=workspace.waveforms(pid,run_id);self.assertIn('display_sampling',parsed)
            response=workspace.operations.invoke('run.waveform_stats',{'project_id':pid,'run_id':run_id})
            self.assertTrue(response['ok'],response)
            # Independent full-file reference: the ASCII column after the time
            # scale is v(out), one record per three lines for this two-node deck.
            text=workspace.artifact(pid,run_id,'waveforms.raw').read_text()
            headers,values=text.split('Values:\n',1)
            width=int(headers.split('No. Variables:')[1].splitlines()[0])
            lines=[line.strip() for line in values.splitlines() if line.strip()]
            original=[float(lines[i+1]) for i in range(0,len(lines),width)]
            self.assertEqual(response['data']['statistics'][1]['count'],len(original))
            self.assertAlmostEqual(result['outputs']['mean'],math.fsum(original)/len(original),places=14)


if __name__=='__main__':unittest.main()
