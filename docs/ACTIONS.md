# Button & action guide

Generated from the live action registry by `scripts/gen_action_docs.py` — every toolbar button,
menu item and shortcut below runs the same handler. *Changes audio* = alters what plays/exports;
*Undoable* = creates one history entry. View/display actions never change audio.
Disabled actions show the reason in their tooltip and in the status bar.

## File

| Action | Shortcut | What it does | Acts on | Changes audio | Undoable | Disabled when | Status |
|---|---|---|---|---|---|---|---|
| **Open Audio…** `file.open_audio` | Ctrl+O | Open a WAV/FLAC/AIFF file (the source file is never modified) | new project | no | no | Always available | implemented |
| **Open Project…** `file.open_project` | Ctrl+Shift+O | Open a saved Spledio project | project file | no | no | Always available | implemented |
| **Save Project** `file.save` | Ctrl+S | Save the editable project (layers, edits, selection), not a flattened WAV | project | no | no | No project open, or an operation is running | implemented |
| **Save Project As…** `file.save_as` | Ctrl+Shift+S | Save the project to a new file | project | no | no | No project open, or an operation is running | implemented |
| **Save Portable Project…** `file.save_portable` | — | Save a copy that embeds the source audio files, so it opens on another computer | project | no | no | No project open, or an operation is running | implemented |
| **Export Audio…** `file.export` | Ctrl+E | Render the current edited audio to WAV/FLAC (choose bit depth, dither, clipping policy) | rendered mix / layers / selection | no | no | No project open, or an operation is running | implemented |
| **Quit** `file.quit` | Ctrl+Q | Quit Spledio | view | no | no | Always available | implemented |

## Edit

| Action | Shortcut | What it does | Acts on | Changes audio | Undoable | Disabled when | Status |
|---|---|---|---|---|---|---|---|
| **Undo** `edit.undo` | Ctrl+Z | Undo the last audio/layer operation (restores exact samples) | project | yes | no | Nothing to undo (or busy) | implemented |
| **Redo** `edit.redo` | Ctrl+Shift+Z / Ctrl+Y | Redo the next operation | project | yes | no | Nothing to redo (or busy) | implemented |
| **Apply Gain** `edit.apply_gain` | Ctrl+G | Change the selected spectral content by the gain set in the edit panel | selection on active layer | yes | yes | No selection, active layer locked, busy, or no project | implemented |
| **Apply -3 dB** `edit.gain_m3` | — | Immediately apply -3 dB to the selected content | selection on active layer | yes | yes | No selection, active layer locked, busy, or no project | implemented |
| **Apply -6 dB** `edit.gain_m6` | — | Immediately apply -6 dB to the selected content | selection on active layer | yes | yes | No selection, active layer locked, busy, or no project | implemented |
| **Apply -12 dB** `edit.gain_m12` | — | Immediately apply -12 dB to the selected content | selection on active layer | yes | yes | No selection, active layer locked, busy, or no project | implemented |
| **Apply +3 dB** `edit.gain_p3` | — | Immediately apply +3 dB to the selected content | selection on active layer | yes | yes | No selection, active layer locked, busy, or no project | implemented |
| **Apply +6 dB** `edit.gain_p6` | — | Immediately apply +6 dB to the selected content | selection on active layer | yes | yes | No selection, active layer locked, busy, or no project | implemented |
| **Reset Gain to 0 dB** `edit.gain_reset` | — | Set the edit-panel gain value back to 0 dB (does not change audio) | view | no | no | Always available | implemented |
| **Mute Region** `edit.mute` | Ctrl+M | Set the selected spectral content to zero gain (reversible) | selection | yes | yes | No selection, active layer locked, busy, or no project | implemented |
| **Delete / Remove Adjustment** `edit.delete_spectral` | Del / Backspace | On a picked adjusted area: remove its adjustment (the audio there comes back exactly). On a new selection: silence the selected sound as a new adjusted area – duration and timing are never changed, and you can bring it back later | selection / picked area | yes | yes | Area keys: need a selection (or picked area) on an unlocked layer. Not blocked while a job
        runs – presses made during a render are queued by the controller.; 
        s = self.ctl.session
        if s is None:
            return ; 
        a = s.current_area()
        if a is None and s.selection is None:
            return ; 
        lay = s.project.layer(a.layer_id) if a is not None else s.active_layer
        if lay is None:
            return ; 
        return f | implemented |
| **Extract to New Layer** `edit.extract` | Ctrl+J | Move the selected content to a new aligned layer; remainder + extract = original at unity gain | selection → new layer | yes | yes | No selection, active layer locked, busy, or no project | implemented |
| **Copy to New Layer** `edit.copy_to_layer` | Ctrl+Shift+J | Duplicate the selected content into a new layer (the summed level can increase) | selection → new layer | yes | yes | No selection (or busy / no project) | implemented |
| **Delete Time (shortens project)…** `edit.delete_time` | Ctrl+Alt+Del | Remove the selection's time span from ALL layers. This shortens the recording | all layers | yes | yes | No selection (or busy / no project) | implemented |
| **Crop Time to Selection…** `edit.crop_time` | — | Keep only the selection's time span in ALL layers | all layers | yes | yes | No selection (or busy / no project) | implemented |
| **Insert Silence…** `edit.insert_silence` | — | Insert silence at the playhead in ALL layers (lengthens the project) | all layers | yes | yes | No project open, or an operation is running | implemented |
| **Louder (1 dB)** `area.louder` | Up / + / = | Raise the level of the selection / picked area by 1 dB. Each adjusted area stays outlined so you can pick it again later | selection / picked area | yes | yes | Area keys: need a selection (or picked area) on an unlocked layer. Not blocked while a job
        runs – presses made during a render are queued by the controller.; 
        s = self.ctl.session
        if s is None:
            return ; 
        a = s.current_area()
        if a is None and s.selection is None:
            return ; 
        lay = s.project.layer(a.layer_id) if a is not None else s.active_layer
        if lay is None:
            return ; 
        return f | implemented |
| **Quieter (1 dB)** `area.quieter` | Down / - | Lower the level of the selection / picked area by 1 dB (below −48 dB: silence). Each adjusted area stays outlined so you can pick it again later | selection / picked area | yes | yes | Area keys: need a selection (or picked area) on an unlocked layer. Not blocked while a job
        runs – presses made during a render are queued by the controller.; 
        s = self.ctl.session
        if s is None:
            return ; 
        a = s.current_area()
        if a is None and s.selection is None:
            return ; 
        lay = s.project.layer(a.layer_id) if a is not None else s.active_layer
        if lay is None:
            return ; 
        return f | implemented |
| **Louder (6 dB)** `area.louder_big` | Shift+Up | Raise the level to the next 6 dB step. Each adjusted area stays outlined so you can pick it again later | selection / picked area | yes | yes | Area keys: need a selection (or picked area) on an unlocked layer. Not blocked while a job
        runs – presses made during a render are queued by the controller.; 
        s = self.ctl.session
        if s is None:
            return ; 
        a = s.current_area()
        if a is None and s.selection is None:
            return ; 
        lay = s.project.layer(a.layer_id) if a is not None else s.active_layer
        if lay is None:
            return ; 
        return f | implemented |
| **Quieter (6 dB)** `area.quieter_big` | Shift+Down | Lower the level to the next 6 dB step. Each adjusted area stays outlined so you can pick it again later | selection / picked area | yes | yes | Area keys: need a selection (or picked area) on an unlocked layer. Not blocked while a job
        runs – presses made during a render are queued by the controller.; 
        s = self.ctl.session
        if s is None:
            return ; 
        a = s.current_area()
        if a is None and s.selection is None:
            return ; 
        lay = s.project.layer(a.layer_id) if a is not None else s.active_layer
        if lay is None:
            return ; 
        return f | implemented |
| **Silence** `area.silence` | 0 | Turn the selection / picked area all the way down (it can be brought back) | selection / picked area | yes | yes | Area keys: need a selection (or picked area) on an unlocked layer. Not blocked while a job
        runs – presses made during a render are queued by the controller.; 
        s = self.ctl.session
        if s is None:
            return ; 
        a = s.current_area()
        if a is None and s.selection is None:
            return ; 
        lay = s.project.layer(a.layer_id) if a is not None else s.active_layer
        if lay is None:
            return ; 
        return f | implemented |
| **Remove Adjustment** `area.remove` | — | Undo the picked area's level change exactly (other areas stay) | picked area | yes | yes | Click an adjusted area first | implemented |
| **Remove All Adjustments** `area.remove_all` | — | Remove every adjusted area (one undo step) | all adjusted areas | yes | yes | There are no adjusted areas yet | implemented |
| **Pick Next Area** `area.next` | ] | Select the next adjusted area | view | no | no | There are no adjusted areas yet | implemented |
| **Pick Previous Area** `area.prev` | [ | Select the previous adjusted area | view | no | no | There are no adjusted areas yet | implemented |
| **Preview** `module.preview` | P | Render the gain edit without applying it, then play it | selection | no | no | No selection, active layer locked, busy, or no project | implemented |
| **Bypass (A/B preview)** `module.bypass` | B | Toggle between the original and the preview while playing | view | no | no | No pending preview | implemented |
| **Apply** `module.apply` | Ctrl+Return / Ctrl+Enter | Commit exactly the previewed result as one undoable operation (without a preview: apply the current gain amount) | selection | yes | yes | ), changes_audio=True, undoable=True, acts_on= | implemented |
| **Cancel Preview** `module.cancel` | — | Discard the preview (or stop a preview that is still rendering); audio is unchanged | view | no | no | cancel | implemented |

## View

| Action | Shortcut | What it does | Acts on | Changes audio | Undoable | Disabled when | Status |
|---|---|---|---|---|---|---|---|
| **Highlight** `view.show_areas` | Shift+H | Show every adjusted area outlined with its level (display only). Turn off to hide them when there are many | view | no | no | Always available | implemented |
| **Zoom In (time)** `view.zoom_in` | Ctrl+= / Ctrl++ | Zoom in around the playhead/centre | view | no | no | No audio/project open | implemented |
| **Zoom Out (time)** `view.zoom_out` | Ctrl+- | Zoom out | view | no | no | No audio/project open | implemented |
| **Zoom In (frequency)** `view.zoom_in_freq` | Ctrl+Shift+Up | Zoom in vertically around the centre | view | no | no | No audio/project open | implemented |
| **Zoom Out (frequency)** `view.zoom_out_freq` | Ctrl+Shift+Down | Zoom out vertically | view | no | no | No audio/project open | implemented |
| **Fit Selection** `view.fit_selection` | Ctrl+Shift+E | Fit the current time-frequency selection in the view | view | no | no | No selection (or busy / no project) | implemented |
| **Fit Project** `view.fit_project` | Ctrl+0 | Show the whole project (view only; the file is never cropped) | view | no | no | No audio/project open | implemented |
| **Reset View** `view.reset` | — | Default viewport and display settings | view | no | no | No audio/project open | implemented |
| **Focus Editing** `view.focus` | F11 | Hide panels to maximise the spectrogram (toggle) | view | no | no | Always available | implemented |
| **Show Overview** `view.overview` | — | Show Overview (display only) | view | no | no | Always available | implemented |
| **Show Waveform** `view.waveform` | — | Show Waveform (display only) | view | no | no | Always available | implemented |
| **Musical Note Labels** `view.notes` | — | Musical Note Labels (display only) | view | no | no | Always available | implemented |
| **Crosshair** `view.crosshair` | — | Crosshair (display only) | view | no | no | Always available | implemented |
| **Logarithmic Frequency Scale** `view.log_scale` | Ctrl+L | Log frequency axis (off = linear). Display only | view | no | no | Always available | implemented |

## Transport

| Action | Shortcut | What it does | Acts on | Changes audio | Undoable | Disabled when | Status |
|---|---|---|---|---|---|---|---|
| **Play / Pause** `transport.play_pause` | Space | Play the audible mix from the playhead, or pause | view | no | no | No audio/project open | implemented |
| **Stop** `transport.stop` | — | Stop playback (the playhead and view stay where they are) | view | no | no | No audio/project open | implemented |
| **Return to Start** `transport.to_start` | Home | Move the playhead to the project start | view | no | no | No audio/project open | implemented |
| **Loop** `transport.loop` | L | Loop the selection's time span (or the whole project) | view | no | no | No audio/project open | implemented |
| **Hear Selected Frequencies** `audition.selected` | S | Play only the selected spectral content (real mask) | view | no | no | No selection (or busy / no project) | implemented |
| **Hear All Frequencies** `audition.all_freqs` | Shift+S | Play the selection's time range without frequency restriction | view | no | no | No selection (or busy / no project) | implemented |
| **Hear Remainder** `audition.remainder` | R | Play the active layer in that span with the selection removed | view | no | no | No selection (or busy / no project) | implemented |
| **Hear What Is Being Removed** `audition.removed` | D | Play original minus processed for the preview (or the last edit), time-aligned | view | no | no | No preview and no audio edit yet | implemented |
| **A/B Compare** `audition.ab` | A | Switch between original (A) and processed (B) without moving the playhead | view | no | no | No preview and no audio edit yet | implemented |

## Select

| Action | Shortcut | What it does | Acts on | Changes audio | Undoable | Disabled when | Status |
|---|---|---|---|---|---|---|---|
| **Select All** `select.all` | Ctrl+A | Select the whole project (all time, all frequencies) | view | no | no | No project open, or an operation is running | implemented |
| **Deselect** `select.none` | Ctrl+D | Remove the selection only (audio unchanged) | view | no | no | No selection (or busy / no project) | implemented |
| **Reselect** `select.reselect` | Ctrl+Shift+D | Restore the previous selection | view | no | no | No project open, or an operation is running | implemented |
| **Invert Selection** `select.invert` | Ctrl+I | Select everything that is not selected | view | no | no | No project open, or an operation is running | implemented |
| **Expand in Time** `select.expand_time` | — | Expand in Time by the step set in the Selection panel | view | no | no | No selection (or busy / no project) | implemented |
| **Contract in Time** `select.contract_time` | — | Contract in Time by the step set in the Selection panel | view | no | no | No selection (or busy / no project) | implemented |
| **Expand in Frequency** `select.expand_freq` | — | Expand in Frequency by the step set in the Selection panel | view | no | no | No selection (or busy / no project) | implemented |
| **Contract in Frequency** `select.contract_freq` | — | Contract in Frequency by the step set in the Selection panel | view | no | no | No selection (or busy / no project) | implemented |
| **Feathering On** `select.feather_toggle` | Ctrl+Shift+F | Temporarily disable/enable feathering (keeps the values) | view | no | no | No selection (or busy / no project) | implemented |
| **Mode: Replace** `select.mode_replace` | — | New selections replace the current one | view | no | no | Always available | implemented |
| **Mode: Add** `select.mode_add` | — | New selections are added (or hold Shift) | view | no | no | Always available | implemented |
| **Mode: Subtract** `select.mode_subtract` | — | New selections are removed from the current one (or hold Alt/Option) | view | no | no | Always available | implemented |
| **Mode: Intersect** `select.mode_intersect` | — | Keep only the overlap (or hold Shift+Alt) | view | no | no | Always available | implemented |

## Tools

| Action | Shortcut | What it does | Acts on | Changes audio | Undoable | Disabled when | Status |
|---|---|---|---|---|---|---|---|
| **Time range** `tool.time` | T | Select a time span across all frequencies (drag left/right) | view/selection | no | no | Always available | implemented |
| **Frequency range** `tool.freq` | F | Select a frequency band over the whole duration (drag up/down) | view/selection | no | no | Always available | implemented |
| **Rectangle** `tool.rect` | M | Drag a time-frequency box | view/selection | no | no | Always available | implemented |
| **Ellipse** `tool.ellipse` | E | Drag an elliptical time-frequency region | view/selection | no | no | Always available | implemented |
| **Freehand lasso** `tool.lasso` | Shift+L | Draw an arbitrary closed selection | view/selection | no | no | Always available | implemented |
| **Polygon lasso** `tool.polygon` | Shift+P | Click vertices; finish with Enter or double-click, cancel with Esc | view/selection | no | no | Always available | implemented |
| **Selection brush** `tool.sel_brush` | Shift+B | Paint a soft selection (does not change audio) | view/selection | no | no | Always available | implemented |
| **Transform selection** `tool.transform` | V | Drag to move the selection mask only (never the sound) | view/selection | no | no | Always available | implemented |
| **Reduce brush** `tool.reduce_brush` | Shift+R | Paint attenuation onto the spectral content (one stroke = one undo) | active layer | yes | yes | Always available | implemented |
| **Amplify brush** `tool.amplify_brush` | Shift+A | Paint a gain boost onto the spectral content (one stroke = one undo) | active layer | yes | yes | Always available | implemented |
| **Transfer brush** `tool.transfer_brush` | Shift+T | Move part of the painted content to another layer (sum preserved) | active layer | yes | yes | Always available | implemented |
| **Hand / pan** `tool.hand` | H | Drag to pan the view | view/selection | no | no | Always available | implemented |
| **Zoom** `tool.zoom` | Z | Click to zoom in at the cursor, Alt+click to zoom out, drag to zoom to a box | view/selection | no | no | Always available | implemented |

## Layer

| Action | Shortcut | What it does | Acts on | Changes audio | Undoable | Disabled when | Status |
|---|---|---|---|---|---|---|---|
| **New Empty Layer** `layer.new` | Ctrl+Shift+N | Add a silent, time-aligned layer | view | no | yes | No project open, or an operation is running | implemented |
| **Duplicate Layer** `layer.duplicate` | — | Duplicate the active layer (summed level increases) | project | yes | yes | No project open, or an operation is running | implemented |
| **Delete Layer** `layer.delete` | — | Delete the active layer (undoable) | project | yes | yes | No project open, or an operation is running | implemented |
| **Move Layer Up** `layer.up` | — | Move the active layer up | view | no | yes | No project open, or an operation is running | implemented |
| **Move Layer Down** `layer.down` | — | Move the active layer down | view | no | yes | No project open, or an operation is running | implemented |
| **Merge All Layers** `layer.merge` | — | Merge every layer into one (at current gains) | project | yes | yes | Only one layer | implemented |
| **Mute Active Layer** `layer.mute_active` | Ctrl+Shift+M | Toggle mute of the active layer | project | yes | yes | No project open, or an operation is running | implemented |
| **Solo Active Layer** `layer.solo_active` | Ctrl+Shift+L | Toggle solo of the active layer | project | yes | yes | No project open, or an operation is running | implemented |
| **Export Active Layer…** `layer.export` | — | Export the active layer only | view | no | no | No project open, or an operation is running | implemented |

## Help

| Action | Shortcut | What it does | Acts on | Changes audio | Undoable | Disabled when | Status |
|---|---|---|---|---|---|---|---|
| **Command Palette…** `help.commands` | Ctrl+K / Ctrl+Shift+P | Search and run any command | view | no | no | Always available | implemented |
| **Tutorials…** `help.tutorials` | F1 | Short guided tutorials | view | no | no | Always available | implemented |
| **Keyboard Shortcuts…** `help.shortcuts` | — | View and remap shortcuts | view | no | no | Always available | implemented |
| **Cancel Running Operation** `help.cancel_job` | — | Cancel the current render/analysis job | view | no | no | , lambda: None if self.ctl.busy else  | implemented |
| **About Spledio** `help.about` | — | Version, license and privacy information | view | no | no | Always available | implemented |

## Mouse & modifiers on the spectrogram

| Gesture | Effect |
|---|---|
| Drag with a selection tool | Create a selection (Replace by default) |
| Shift + drag | Add to selection |
| Alt/Option + drag | Subtract from selection |
| Shift + Alt/Option + drag | Intersect with selection |
| Click (no drag) | Move the playhead there (selection kept) |
| Drag a rectangle handle | Resize the rectangle selection |
| Wheel / trackpad | Pan time |
| Ctrl/Cmd + wheel | Zoom time around the cursor |
| Shift + wheel | Zoom frequency around the cursor |
| Esc | Cancel drawing → cancel running job → cancel preview → clear selection (in that order) |
| Enter / double-click | Finish polygon lasso |

Single-key shortcuts (Space, P, B, A, S, R, D, L, T, F, …) are suspended while a text or number field has focus,
so typing a value never triggers playback or an edit. Shortcuts can be remapped in Help → Keyboard Shortcuts.
