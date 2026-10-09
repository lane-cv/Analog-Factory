v {xschem version=3.4.7 file_version=1.2}
G {}
K {}
V {}
S {}
E {}
T {Five-transistor OTA | SKY130A | 1.8 V | external bias 0.65 V} 100 30 0 0 0.35 0.35 {}
T {M1/M2: differential pair   M3/M4: current mirror   M5: tail source} 100 70 0 0 0.25 0.25 {}
C {devices/netlist_options.sym} 1050 110 0 0 {name=OPTIONS top_is_subckt=true}
C {sky130_fd_pr/nfet_01v8.sym} 400 400 0 0 {name=M1 L=2 W=4 nf=1 mult=1 model=nfet_01v8 spiceprefix=X
ad="expr('int((@nf + 1)/2) * @W / @nf * 0.29')"
as="expr('int((@nf + 2)/2) * @W / @nf * 0.29')"
pd="expr('2*int((@nf + 1)/2) * (@W / @nf + 0.29)')"
ps="expr('2*int((@nf + 2)/2) * (@W / @nf + 0.29)')"
nrd="expr('0.29 / @W ')" nrs="expr('0.29 / @W ')" sa=0 sb=0 sd=0}
C {sky130_fd_pr/nfet_01v8.sym} 800 400 0 0 {name=M2 L=2 W=4 nf=1 mult=1 model=nfet_01v8 spiceprefix=X
ad="expr('int((@nf + 1)/2) * @W / @nf * 0.29')"
as="expr('int((@nf + 2)/2) * @W / @nf * 0.29')"
pd="expr('2*int((@nf + 1)/2) * (@W / @nf + 0.29)')"
ps="expr('2*int((@nf + 2)/2) * (@W / @nf + 0.29)')"
nrd="expr('0.29 / @W ')" nrs="expr('0.29 / @W ')" sa=0 sb=0 sd=0}
C {sky130_fd_pr/pfet_01v8.sym} 400 220 0 0 {name=M3 L=2 W=8 nf=1 mult=1 model=pfet_01v8 spiceprefix=X
ad="expr('int((@nf + 1)/2) * @W / @nf * 0.29')"
as="expr('int((@nf + 2)/2) * @W / @nf * 0.29')"
pd="expr('2*int((@nf + 1)/2) * (@W / @nf + 0.29)')"
ps="expr('2*int((@nf + 2)/2) * (@W / @nf + 0.29)')"
nrd="expr('0.29 / @W ')" nrs="expr('0.29 / @W ')" sa=0 sb=0 sd=0}
C {sky130_fd_pr/pfet_01v8.sym} 800 220 0 0 {name=M4 L=2 W=8 nf=1 mult=1 model=pfet_01v8 spiceprefix=X
ad="expr('int((@nf + 1)/2) * @W / @nf * 0.29')"
as="expr('int((@nf + 2)/2) * @W / @nf * 0.29')"
pd="expr('2*int((@nf + 1)/2) * (@W / @nf + 0.29)')"
ps="expr('2*int((@nf + 2)/2) * (@W / @nf + 0.29)')"
nrd="expr('0.29 / @W ')" nrs="expr('0.29 / @W ')" sa=0 sb=0 sd=0}
C {sky130_fd_pr/nfet_01v8.sym} 600 600 0 0 {name=M5 L=2 W=4 nf=1 mult=1 model=nfet_01v8 spiceprefix=X
ad="expr('int((@nf + 1)/2) * @W / @nf * 0.29')"
as="expr('int((@nf + 2)/2) * @W / @nf * 0.29')"
pd="expr('2*int((@nf + 1)/2) * (@W / @nf + 0.29)')"
ps="expr('2*int((@nf + 2)/2) * (@W / @nf + 0.29)')"
nrd="expr('0.29 / @W ')" nrs="expr('0.29 / @W ')" sa=0 sb=0 sd=0}
N 420 140 820 140 {lab=vdd}
N 420 140 420 190 {lab=vdd}
N 820 140 820 190 {lab=vdd}
N 420 250 420 300 {lab=nmirror}
N 420 300 420 370 {lab=nmirror}
N 820 250 820 370 {lab=out}
N 340 220 380 220 {lab=nmirror}
N 340 220 340 300 {lab=nmirror}
N 340 300 740 300 {lab=nmirror}
N 740 220 740 300 {lab=nmirror}
N 740 220 780 220 {lab=nmirror}
N 420 430 420 500 {lab=tail}
N 820 430 820 500 {lab=tail}
N 420 500 820 500 {lab=tail}
N 620 500 620 570 {lab=tail}
N 620 630 620 680 {lab=vss}
C {devices/lab_pin.sym} 620 140 0 0 {name=L0 lab=vdd}
C {devices/lab_pin.sym} 420 400 0 0 {name=L1 lab=vss}
C {devices/lab_pin.sym} 820 400 0 0 {name=L2 lab=vss}
C {devices/lab_pin.sym} 420 220 0 0 {name=L3 lab=vdd}
C {devices/lab_pin.sym} 820 220 0 0 {name=L4 lab=vdd}
C {devices/lab_pin.sym} 620 600 0 0 {name=L5 lab=vss}
C {devices/lab_pin.sym} 620 680 0 0 {name=L6 lab=vss}
C {devices/lab_pin.sym} 380 400 0 0 {name=L7 lab=inp}
C {devices/lab_pin.sym} 780 400 0 0 {name=L8 lab=inn}
C {devices/lab_pin.sym} 580 600 0 0 {name=L9 lab=bias}
C {devices/lab_pin.sym} 820 310 0 0 {name=L10 lab=out}
C {devices/lab_pin.sym} 420 300 0 0 {name=L11 lab=nmirror}
C {devices/lab_pin.sym} 620 500 0 0 {name=L12 lab=tail}
C {devices/ipin.sym} 100 170 0 0 {name=P1 lab=inp}
C {devices/ipin.sym} 100 230 0 0 {name=P2 lab=inn}
C {devices/ipin.sym} 100 290 0 0 {name=P3 lab=bias}
C {devices/iopin.sym} 100 350 0 0 {name=P4 lab=vdd}
C {devices/iopin.sym} 100 410 0 0 {name=P5 lab=vss}
C {devices/opin.sym} 100 470 0 0 {name=P6 lab=out}
