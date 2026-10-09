/* Pure display transformations. Recorded raw data is never changed. */
((root) => {
  'use strict';
  const components = ['magnitude','real','imaginary','phase','db'];
  function plots(raw) { return raw ? (raw.plots?.length ? raw.plots : [raw]) : []; }
  function view(raw, options = {}) {
    const plotIndex = options.plot ?? 0, component = options.component ?? 'magnitude';
    const available = plots(raw);
    if (!Number.isInteger(plotIndex) || plotIndex < 0 || plotIndex >= available.length) throw new Error('Unknown waveform plot.');
    if (!components.includes(component)) throw new Error('Unknown waveform component.');
    const selected = available[plotIndex], variables = selected.variables, points = selected.points;
    const complex = selected.complex_points, reference = options.reference ?? null;
    if (!Array.isArray(variables) || !Array.isArray(points)) throw new Error('Invalid waveform data.');
    if (reference !== null && (!Number.isInteger(reference) || reference < 1 || reference >= variables.length)) throw new Error('Unknown reference trace.');
    if (['phase','imaginary'].includes(component) && !complex) throw new Error('This component requires complex waveform data.');
    if (complex && (complex.length !== points.length || complex.some(row => !Array.isArray(row) || row.length !== variables.length || row.some(pair => !Array.isArray(pair) || pair.length !== 2 || !pair.every(Number.isFinite))))) throw new Error('Invalid complex waveform dimensions.');
    let undefinedValues = 0;
    const previous = new Map();
    const transformed = points.map((row, rowIndex) => row.map((value, index) => {
      if (!index) return value;
      let [real, imaginary] = complex ? complex[rowIndex][index] : [value,0];
      if (reference !== null) {
        const [refReal,refImaginary] = complex ? complex[rowIndex][reference] : [row[reference],0];
        const size = refReal*refReal+refImaginary*refImaginary;
        if (!size) { undefinedValues++; previous.delete(index); return null; }
        [real,imaginary] = [(real*refReal+imaginary*refImaginary)/size,(imaginary*refReal-real*refImaginary)/size];
      }
      const magnitude = Math.hypot(real,imaginary);
      let output;
      if (component === 'real') output = real;
      else if (component === 'imaginary') output = imaginary;
      else if (component === 'db') output = magnitude > 0 ? 20*Math.log10(magnitude) : null;
      else if (component === 'phase') {
        output = magnitude > 0 ? Math.atan2(imaginary,real)*180/Math.PI : null;
        if (output !== null && options.unwrap && previous.has(index)) {
          output += 360*Math.round((previous.get(index)-output)/360);
        }
        if (output === null) previous.delete(index); else previous.set(index,output);
      } else output = complex ? magnitude : real;
      if (!Number.isFinite(output)) { undefinedValues++; return null; }
      return output;
    }));
    const labels = variables.map((variable,index) => {
      const result = {...variable};
      if (!index) return result;
      let unit = variable.unit || '';
      if (reference !== null) {
        result.name = `${variable.name} / ${variables[reference].name}`;
        unit = unit === (variables[reference].unit || '') ? '' : `${unit || '1'}/${variables[reference].unit || '1'}`;
      }
      result.component = component;
      result.unit = component === 'phase' ? '°' : component === 'db' ? (unit ? `dB re 1 ${unit}` : 'dB') : unit;
      return result;
    });
    return {title:selected.title,plot:selected.plot,variables:labels,points:transformed,
      display:{plot:plotIndex,component,reference,unwrap:Boolean(options.unwrap)},undefined_values:undefinedValues};
  }
  function axis(values, scale = 'linear') {
    if (!['linear','log'].includes(scale)) throw new Error('Unknown horizontal scale.');
    if (!values.length || values.some(value => !Number.isFinite(value))) throw new Error('Invalid horizontal values.');
    if (values.some((value,index) => index && value < values[index-1])) throw new Error('Horizontal samples must be increasing.');
    if (scale === 'log' && values.some(value => value <= 0)) throw new Error('Logarithmic axes require positive horizontal values.');
    const forward = scale === 'log' ? Math.log10 : value => value;
    const inverse = scale === 'log' ? value => 10**value : value => value;
    return {forward,inverse,bounds:[forward(values[0]),forward(values.at(-1))]};
  }
  function csv(waveform) {
    const quote = value => /[",\r\n]/.test(value) ? `"${value.replaceAll('"','""')}"` : value;
    return waveform.variables.map(variable => quote(`${variable.name}${variable.unit ? ` [${variable.unit}]` : ''}`)).join(',') + '\r\n' +
      waveform.points.map(row => row.map(value => Number.isFinite(value) ? String(value) : '').join(',')).join('\r\n') + '\r\n';
  }
  const api = {plots,view,axis,csv,components};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.AFWaveforms = api;
})(typeof window === 'undefined' ? globalThis : window);
