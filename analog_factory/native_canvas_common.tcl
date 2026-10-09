# Fixed data-only transport. Request contents are never sourced or evaluated.
namespace eval afcanvas {
    variable revision 0
    variable previous {}
    variable seen {}
    variable packet_limit 131072
    variable document_limit 2097152
}
proc afcanvas::read_file {path limit} {
    if {[file type $path] ne "file" || [file size $path] > $limit} {error "Private file is not bounded and regular"}
    set f [open $path rb]
    try {set data [read $f [expr {$limit + 1}]]} finally {close $f}
    if {[string length $data] > $limit} {error "Private file exceeds its bound"}
    return $data
}
proc afcanvas::write_file {path data} {
    set f [open "$path.tmp" wb]
    try {puts -nonewline $f $data} finally {close $f}
    file rename -force "$path.tmp" $path
}
proc afcanvas::decode {data} {
    variable packet_limit
    if {[string length $data] > $packet_limit} {error "Request too large"}
    set result {}
    foreach line [split $data \n] {
        if {$line eq {}} continue
        if {![regexp {^([a-z_]+)=((?:[a-f0-9]{2})*)$} $line -> key encoded] || [dict exists $result $key]} {error "Malformed request"}
        if {$key ni {nonce command_id verb expected_revision instance_id direction grid_steps from_instance from_pin to_instance to_pin route box_token layer commit document_sha256}} {error "Unknown request field"}
        dict set result $key [encoding convertfrom utf-8 [binary decode hex $encoded]]
    }
    return $result
}
proc afcanvas::encode {data} {
    set result {}
    dict for {key value} $data {append result "$key=[binary encode hex [encoding convertto utf-8 $value]]\n"}
    return $result
}
proc afcanvas::quote_json {s} {
    set out {"}
    foreach c [split $s {}] {
        scan $c %c n
        if {$n == 34} {append out {\"}} elseif {$n == 92} {append out {\\}} elseif {$n < 32} {append out [format {\u%04x} $n]} else {append out $c}
    }
    append out {"};return $out
}
proc afcanvas::json_array {values} {
    set out {};foreach value $values {lappend out [quote_json $value]}
    return "\[[join $out ,]\]"
}
proc afcanvas::milliseconds {} {
    # Magic renames Tcl's clock to orig_clock for its own native namespace.
    if {[llength [info commands ::orig_clock]]} {return [::orig_clock milliseconds]}
    return [::clock milliseconds]
}
proc afcanvas::observe {} {
    variable previous;variable revision
    set state [snapshot]
    set document [dict get $state document]
    if {[string length [encoding convertto utf-8 $document]] > 2097152} {error "Document exceeds 2 MiB"}
    set fingerprint [list [dict get $state source] [dict get $state modified] $document [dict get $state selected] [dict get $state box]]
    if {$fingerprint ne $previous} {incr revision;set previous $fingerprint}
    dict set state revision $revision
    return $state
}
proc afcanvas::validate {packet} {
    if {[dict get $packet nonce] ne $::af_nonce || ![regexp {^[a-f0-9]{32}$} [dict get $packet command_id]]} {error "Session identity mismatch"}
    if {[dict get $packet verb] ni {inspect select move connect paint save_prepare}} {error "Unsupported semantic verb"}
    if {![regexp {^[0-9]{1,12}$} [dict get $packet expected_revision]]} {error "Invalid native revision"}
    foreach key {instance_id from_instance from_pin to_instance to_pin layer} {
        if {[dict exists $packet $key] && ![regexp {^[A-Za-z_][A-Za-z0-9_.:/+-]{0,127}$} [dict get $packet $key]]} {error "Invalid semantic identifier"}
    }
    if {[dict get $packet verb] eq "move"} {
        if {[dict get $packet direction] ni {left right up down} || ![regexp {^[1-9][0-9]{0,2}$} [dict get $packet grid_steps]]} {error "Invalid bounded movement"}
    }
    if {[dict get $packet verb] eq "connect" && [dict get $packet route] ni {horizontal_first vertical_first}} {error "Invalid pin route"}
}
proc afcanvas::ack {id state ok {code {}} {message {}}} {
    set document [dict get $state document]
    write_file [file join $::af_root "$id.document"] [encoding convertto utf-8 $document]
    dict set state document "$id.document"
    dict set state nonce $::af_nonce
    dict set state command_id $id
    dict set state ok $ok
    dict set state error_code $code
    dict set state error $message
    dict set state complete 1
    write_file [file join $::af_root "$id.response"] [encode $state]
}
proc afcanvas::poll {} {
    variable seen
    foreach path [glob -nocomplain -directory $::af_root *.request] {
        set id [file rootname [file tail $path]]
        if {![regexp {^[a-f0-9]{32}$} $id] || [dict exists $seen $id]} continue
        dict set seen $id 1
        # Consume before execution: duplicate request IDs can never repeat edits.
        if {[dict size $seen] > 256} {file delete $path;continue}
        set state {};set code EXECUTION_FAILED
        if {[catch {
            set packet [decode [read_file $path 131072]]
            file delete $path
            validate $packet
            if {[dict get $packet command_id] ne $id} {error "Command identity mismatch"}
            set state [observe]
            if {[dict get $packet verb] ne "inspect" && [dict get $packet expected_revision] ne [dict get $state revision]} {set code VERSION_CONFLICT;error "Native unsaved state changed; inspect again"}
            if {[dict get $state source] ne $::af_source} {set code VERSION_CONFLICT;error "Current native source changed"}
            if {[dict get $packet verb] eq "save_prepare"} {
                dict set state commit prepared
                ack $id $state 1
                # Keep the GUI event callback atomic across ProjectStore commit.
                # No update/vwait: human input stays queued for at most 2 seconds.
                set deadline [expr {[milliseconds]+2000}]
                set commit [file join $::af_root "$id.commit"]
                set outcome expired
                while {![file exists $commit] && [milliseconds] < $deadline} {after 5}
                if {[file exists $commit]} {
                    set receipt [decode [read_file $commit 131072]]
                    file delete $commit
                    if {[dict get $receipt nonce] ne $::af_nonce || [dict get $receipt command_id] ne $id} {error "Invalid commit receipt"}
                    if {[dict get $receipt commit] eq "saved"} {saved;set outcome saved} else {set outcome cancelled}
                }
                set state [observe]
                dict set state commit $outcome
                ack $id $state 1
            } else {
                if {[dict get $packet verb] ne "inspect"} {dispatch $packet}
                set state [observe]
                ack $id $state 1
            }
        } message]} {
            if {$state ne {}} {catch {ack $id $state 0 $code $message}}
        }
    }
    after 30 afcanvas::poll
}
file mkdir $::af_root
if {[catch {afcanvas::ack 00000000000000000000000000000000 [afcanvas::observe] 1} error]} {
    set f [open [file join $::af_root startup-error.txt] w];puts $f $error;close $f
} else {after 30 afcanvas::poll}
