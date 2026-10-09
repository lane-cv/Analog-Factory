# Native Xschem 3.4.7 primitives. Full instance-only selections are qualified;
# other selections are rejected before exporting, never silently cleared.
namespace eval afcanvas {}
proc afcanvas::brace {s} {
    set out \{
    foreach c [split $s {}] {scan $c %c n;if {$n in {92 123 125}} {append out \\};append out $c}
    append out \};return $out
}
proc afcanvas::snapshot {} {
    xschem rebuild_selection
    set globals [xschem globals]
    regexp {lastsel=([0-9]+)} $globals -> count
    set selection [xschem selected_set]
    if {$count != [llength $selection]} {error "Noninstance/partial native selections are not yet qualified; clear them manually"}
    if {[xschem get semaphore] != 0 || ([xschem get ui_state] & ~8) != 0} {error "Native editor is busy drawing or editing"}
    if {[xschem get schname] ne $::af_source} {error "Native current source differs from the managed source"}
    set instances {};set ids {}
    foreach {id symbol type} [xschem instance_list] {
        if {$id in $ids || $id eq {}} {error "Native instances require unique nonempty names"}
        lappend ids $id
        if {$type eq "missing" || [xschem getprop instance $id embed] ni {{} false 0}} {error "Missing or embedded symbols are not qualified for native export"}
        set coord [xschem instance_coord $id]
        foreach number [lrange $coord 2 3] {if {![string is double -strict $number] || abs($number)>1000000000} {error "Native instance coordinate exceeds its bound"}}
        set pins [xschem instance_pins $id]
        if {[llength $pins]>128} {error "Native instance pin count exceeds its bound"}
        lappend instances "\[[join [list [quote_json $id] [quote_json $symbol] [quote_json $type] {*}[lrange $coord 2 5] [json_array $pins]] ,]\]"
    }
    if {[llength $ids] > 512 || [xschem get wires] > 4096} {error "Native schematic exceeds its instance/wire bound"}
    # The native C clipboard location is initialized before --preinit. A private
    # HOME isolates it from a human's existing editor configuration/clipboard.
    set clip [file join $::env(HOME) .xschem .clipboard.sch]
    set existed [file exists $clip]
    set previous {};if {$existed} {set previous [read_file $clip 2097152]}
    set before_modified [xschem get modified]
    try {
        xschem select_all
        xschem copy
        set content [encoding convertfrom utf-8 [read_file $clip 2097152]]
        set records {};set pending {}
        foreach line [split $content \n] {
            append pending $line \n
            if {[info complete $pending]} {lappend records [string trimright $pending \n];set pending {}}
        }
        if {$pending ne {} || [llength $records] < 2} {error "Incomplete native clipboard serialization"}
        set header "xschem version=3.4.7 file_version=1.2\n[xschem get header_text]"
        set document "v [brace $header]\n"
        foreach {record field} {G schvhdlprop K schsymbolprop V schverilogprop S schprop E schtedaxprop} {append document "$record [brace [xschem get $field]]\n"}
        foreach record [lrange $records 2 end] {if {$record ne {}} {append document $record \n}}
    } finally {
        xschem unselect_all
        foreach id $selection {xschem select instance $id fast}
        if {$existed} {write_file $clip $previous} elseif {[file exists $clip]} {file delete $clip}
    }
    if {[xschem get modified] != $before_modified || [xschem selected_set] ne $selection || [xschem get schname] ne $::af_source} {error "Native export changed editor state"}
    return [dict create source [xschem get schname] modified $before_modified instances "\[[join $instances ,]\]" selected [json_array $selection] box {[]} layers {[]} document $document tool xschem busy 0]
}
proc afcanvas::require_instance {id} {
    set found 0;foreach {name symbol type} [xschem instance_list] {if {$name eq $id} {incr found}}
    if {$found != 1} {error "Native instance does not exist uniquely"}
}
proc afcanvas::dispatch {p} {
    set verb [dict get $p verb]
    if {$verb in {select move}} {
        set id [dict get $p instance_id];require_instance $id
        xschem unselect_all
        if {![xschem select instance $id]} {error "Native selection failed"}
        if {$verb eq "move"} {
            set amount [expr {[dict get $p grid_steps] * $::cadsnap}]
            if {$amount <= 0 || $amount > 100000} {error "Native grid movement exceeds its bound"}
            set dx 0;set dy 0
            switch [dict get $p direction] {left {set dx [expr {-$amount}]} right {set dx $amount} up {set dy [expr {-$amount}]} down {set dy $amount}}
            xschem move_objects $dx $dy stretch
            xschem unselect_all
            xschem select instance $id
        }
    } elseif {$verb eq "connect"} {
        set a [dict get $p from_instance];set b [dict get $p to_instance]
        require_instance $a;require_instance $b
        set first [xschem instance_pin_coord $a name [dict get $p from_pin]]
        set last [xschem instance_pin_coord $b name [dict get $p to_pin]]
        if {[llength $first] != 3 || [llength $last] != 3} {error "Named native pin does not exist"}
        lassign $first name x1 y1;lassign $last name x2 y2
        if {abs($x2-$x1)+abs($y2-$y1)>100000} {error "Native pin route exceeds its 100000-unit bound"}
        if {$x1 == $x2 && $y1 == $y2} {error "Pins already occupy the same native point"}
        if {[dict get $p route] eq "horizontal_first"} {set xm $x2;set ym $y1} else {set xm $x1;set ym $y2}
        if {$x1 != $xm || $y1 != $ym} {xschem wire $x1 $y1 $xm $ym}
        if {$xm != $x2 || $ym != $y2} {xschem wire $xm $ym $x2 $y2}
    } else {error "This native tool does not implement that verb"}
}
proc afcanvas::saved {} {
    # Disk commit is managed by ProjectStore. Keeping modified set avoids
    # changing native timestamps or losing work through a second native write.
    # The response explicitly reports whether the editor still marks it dirty.
    return
}
