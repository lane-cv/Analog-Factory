namespace eval afcanvas {}
proc afcanvas::snapshot {} {
    set current [cellname list window]
    if {$current ne [file rootname [file tail $::af_source]]} {error "Native current cell differs from the bound source"}
    if {[cellname list children $current] ne {}} {error "Hierarchical native layout export is not yet qualified"}
    # Native background DRC replaces transient checkpaint/error planes. Finish
    # that work before serialization so it cannot race the revision guard.
    drc catchup
    set archive [file join $::af_root database.archive]
    archive write $archive
    set document [encoding convertfrom utf-8 [read_file $archive 2097152]]
    set modified [expr {"modified" in [cellname flags]}]
    set selection [what -list]
    set box [box values]
    set layers [tech layers *]
    return [dict create source $::af_source modified $modified instances {[]} selected [json_array $selection] box "\[[join $box ,]\]" layers [json_array $layers] document $document tool magic busy 0]
}
proc afcanvas::dispatch {p} {
    if {[dict get $p verb] ne "paint"} {error "This native tool does not implement that verb"}
    set layer [dict get $p layer]
    if {$layer ni [tech layers *]} {error "Named native layer is unavailable"}
    set expected [dict get $p box_token]
    if {$expected ne [join [box values] ,]} {error "Native cursor box changed; inspect again"}
    lassign [box values] x1 y1 x2 y2
    if {$x2 <= $x1 || $y2 <= $y1 || ($x2-$x1) > 10000 || ($y2-$y1) > 10000} {error "Native box is empty or exceeds the 10000-unit bound"}
    paint $layer
}
proc afcanvas::saved {} {return}
