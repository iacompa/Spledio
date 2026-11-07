# Spledio user manual

Spledio is a free audio editor that shows a recording as a **picture of its sound** (a spectrogram).
You can draw around one sound, such as a ringing tone, a whistle, a cough or a hiss, and make just that
sound quieter, louder or silent. Everything else stays untouched and no time is cut out.

Spledio runs entirely on your computer: no account, no uploads, no internet connection.
It is free and open source under the **GNU General Public License v3.0 only (GPL-3.0-only)**.

> Shortcuts are written for a Mac (**⌘** = Command). On Windows and Linux use **Ctrl** instead of ⌘.

---

## 1. Install

### Mac

1. Download **Spledio-macOS-arm64.dmg** from the official download page or from the
   [GitHub releases page](https://github.com/iacompa/Spledio/releases/latest).
2. Open the downloaded file and drag **Spledio** into **Applications**.
3. Open Spledio from Applications. The app is signed and notarized by Apple, so it opens like any other app.

Requirements: a Mac with Apple Silicon (M1 or newer) running **macOS 15 Sequoia or later**. You don't
need to install anything else: everything Spledio needs is inside the app.

### Windows and Linux

No ready-made installer yet. You can run Spledio from the source code (instructions in the
[README on GitHub](https://github.com/iacompa/Spledio#readme)). Windows and Linux have not been tested yet.

---

## 2. Your first edit (2 minutes)

1. **Open a recording**: drag an audio file onto the window, or **File → Open Audio…** (⌘O).
   WAV, AIFF, FLAC, OGG and MP3 work. Your original file is never changed.
2. **Draw a box** around the sound you want to change. The Rectangle tool (**M**) is selected by default.
   *Left to right* is time and *bottom to top* is pitch, from low to high.
3. A small **level bar** appears next to the box:

   `Selection   [−]  ━━━━●━━━━  [+]   0 dB   [▶]  [Silence]`

   * Click **−** to make that part quieter, or **+** to make it louder: **1 dB per click**. Hold the button to keep going.
   * Or **drag the slider**. Far left means silent.
   * **▶** plays that part in the full mix so you hear the result in context.
4. Listen with **Space** (play/pause). Changes are heard while it plays.
5. **Export** the result with **File → Export Audio…** (⌘E).

That's it. The rest of this manual explains what else you can do.

---

## 3. Reading the picture

| What you see | What it means |
|---|---|
| Left → right | Time |
| Bottom → top | Pitch, from low (bass) to high (hiss, cymbals) |
| Brightness | Loudness. Bright means loud, dark means quiet |
| Thin horizontal line | A steady tone: a note, a hum, a whistle or a ringing resonance |
| Thin vertical line | A short click or hit |
| Fuzzy area | Noise, such as hiss, breath or wind |

Hover the mouse over the picture to read the exact time, pitch (Hz and musical note) and level at the
bottom of the window.

**Zoom:** the bar under the spectrogram has **Zoom − / +** (time), **Frequency − / +** (pitch, the height),
**Whole File** and **Selection**. On a trackpad, **pinch** over the spectrogram to zoom in time.
Keys: ⌘= / ⌘− (time), ⌘⇧↑ / ⌘⇧↓ (pitch), ⌘0 shows the whole file, ⌘⇧E zooms to the selection.
Scroll to move along in time, ⌘+scroll zooms time, Shift+scroll zooms pitch. **H** is the hand tool for dragging the view.

---

## 4. Changing parts of the sound (adjusted areas)

Every part whose level you change becomes an **adjusted area**. It stays outlined on the picture with
its name and level, for example *Area 1 · −6 dB*, so you can always see what you changed.

### With the mouse

* Draw a box, then use **− / +**, the slider, **Silence** or **✕** on the level bar.

### With the keyboard

Click the picture first so it has keyboard focus. The hint at the bottom of the picture shows the keys.

| Key | Does |
|---|---|
| **↓** or **−** | 1 dB quieter |
| **↑** or **+** | 1 dB louder |
| **Shift+↓ / Shift+↑** | 6 dB quieter / louder |
| **0** | Silence it |
| **Delete** | Remove the adjustment from the picked area |
| **[** and **]** | Jump to the previous / next area |

Below −48 dB the next step is silence. Going back up from silence returns to −48 dB. Nothing is lost:
silenced audio can always be brought back.

### Working with several areas

* Draw another box and change it. Each area keeps its **own level**, even where areas overlap.
* **Click inside an outlined area** to pick it again. The level bar and the keys then change that area.
* **Delete**, or **✕** on the level bar, removes an area's change. The audio there comes back **exactly**,
  and the other areas are not affected.
* **Show / Hide** next to *Adjusted areas*, at the bottom right under the spectrogram (or **Shift+H**),
  shows or hides the outlines of every part you changed. Shown by default. This only changes the display,
  not the sound. The picked area stays visible.
* The **Adjusted Areas** panel lists every area. You can type a level, silence an area, remove one, or
  remove all of them at once.

Quick repeated clicks on the same area count as **one** undo step.

---

## 5. Listening

| Key | Plays |
|---|---|
| **Space** | Play / pause the whole mix |
| **S** | Only the selected sound, so you can check you boxed the right thing |
| **Shift+S** | The selected time with all pitches |
| **R** | Everything *except* the selection |
| **D** | Exactly what the last edit removed (the difference), including a level change on an area |
| **L** | Loop the selection |
| **Home** | Back to the start |

The volume slider in the toolbar only changes how loud *you* hear it. It does not change the exported file.

---

## 6. Finding problem sounds automatically

**Resonance Workflow panel → Find candidates** lists narrow frequencies that keep ringing (a "boomy" or
"whistly" tone). Click **Accept** on a result to draw the box around it for you. Then listen with **S**: a musical note can look
like a resonance, so nothing is changed automatically.

---

## 7. More precise editing

For careful work, the **Reduce Selected Sound** panel offers a preview workflow:

1. Set an amount, for example −12 dB.
2. **Preview (P)** plays the result without applying it.
3. **Bypass (B)** or **A/B (A)** switch between original and preview while playing. Without a preview,
   **A/B** compares the last edit, an area turned up or down with ↑/↓ included. After a time edit
   (delete, crop, insert) there is nothing older to compare until you edit again.
4. **Apply** (⌘Return) applies it as one undo step, or **Cancel** discards the preview.

Other tools, all undoable:

* **Selection shapes:** time range (**T**), frequency range (**F**), rectangle (**M**), ellipse (**E**),
  freehand lasso (**Shift+L**), polygon (**Shift+P**) and selection brush (**Shift+B**).
  Hold **Shift** to add to a selection and **Alt/Option** to subtract.
* **Brushes:** reduce (**Shift+R**), amplify (**Shift+A**) and transfer to another layer (**Shift+T**).
  One stroke is one undo step.
* **Layers:** extract (⌘J) or copy (⌘⇧J) the selected sound to its own layer, then mute, solo or change
  the level of each layer. A new layer starts at the level, polarity, mute and solo of the layer it came
  from, so the mix sounds the same until you change it. You can export layers as separate files (stems).
* **Time:** delete or crop time, and insert silence. These change the length of the whole project.
  Delete and crop use the time span you drew; the selection's feather does not widen it.

---

## 8. Undo and history

* **⌘Z** undoes and **⌘⇧Z** redoes. Up to 200 steps. After more than 200 edits the oldest ones can no longer
  be undone, and the project counts as changed until you save it, even after undoing everything that is left.
* The **History** panel lists every step. Click one to jump back to it.

---

## 9. Saving and exporting

* **Save Project** (⌘S) stores a `.spledio` file with your layers, areas and edits. Reopening it gives
  exactly the same sound, and your adjusted areas can still be changed.
  The undo history itself is not saved. If the original audio file was changed by another program while
  the project was open, saving still works: the project then keeps a full copy of the edited audio
  instead of referring to the changed file (a larger file).
* **Portable project** also stores a copy of the original audio inside the project file, so it still
  works if you move or delete the original.
* **Export Audio** (⌘E) writes WAV (16-bit, 24-bit or 32-bit float) or FLAC (16 or 24-bit), at the
  original or a new sample rate. What you export is exactly what you heard.
  * If boosting made the sound too loud for 16/24-bit, you choose what happens: stop, lower the
    level, limit, or clip. 32-bit float never clips.
  * 16/24-bit exports get TPDF dither when samples must be rounded. When nothing needs rounding,
    such as an unedited 16-bit file saved as 16 or 24-bit, the samples are written unchanged.
  * Spledio never overwrites your original audio file.
* **Autosave:** while you have unsaved changes, Spledio saves a recovery copy every 2 minutes.
  After a crash it offers to restore your work.

---

## 10. Keyboard shortcuts (most used)

| | |
|---|---|
| Open audio / project | ⌘O / ⌘⇧O |
| Save / Save as | ⌘S / ⌘⇧S |
| Export | ⌘E |
| Undo / Redo | ⌘Z / ⌘⇧Z |
| Play / pause | Space |
| Hear selection / difference | S / D |
| Quieter / louder 1 dB | ↓ / ↑ (or − / +) |
| Quieter / louder 6 dB | Shift+↓ / Shift+↑ |
| Silence / remove adjustment | 0 / Delete |
| Show / hide adjusted areas | Shift+H |
| Rectangle / time / frequency tool | M / T / F |
| Select all / deselect | ⌘A / ⌘D |
| Zoom time / pitch | ⌘= ⌘− / ⌘⇧↑ ⌘⇧↓ |
| Whole file | ⌘0 |
| Find any command | ⌘K |
| Built-in tutorials | F1 |

Every menu item, button and shortcut is listed in [ACTIONS.md](ACTIONS.md). You can change shortcuts in
the keyboard shortcut editor.

---

## 11. Troubleshooting

| Problem | Fix |
|---|---|
| The arrow keys do nothing | Click the picture first so it has keyboard focus. Arrow keys inside lists and number boxes keep working as usual. |
| "Select something first" | Draw a box on the picture, or click an outlined area. |
| No sound | Spledio plays through the Mac's current output (**System Settings → Sound → Output**). Check that and the volume slider in the toolbar, then press Play again. |
| A selection is refused as "too small" | Very thin or very short boxes fall between analysis points. Make the box a little bigger, or change *Processing resolution* in the Edit panel. |
| "Layer is locked" | Unlock the layer in the Layers panel. |
| The edges of an edit sound soft | That is normal for spectral edits. Use *feather* in the Selection panel for smoother or sharper edges. |
| macOS says the app "can't be opened" | Only download Spledio from the official download page or the GitHub releases page. Official builds are signed and notarized. |

Known limitations are listed in [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md).

---

## 12. Privacy

Spledio does not connect to the internet, has no accounts and collects no data. Your audio never leaves
your computer. Settings, autosave copies and temporary undo data are stored locally; the exact folders are
listed in [PRIVACY_AND_STORAGE.md](PRIVACY_AND_STORAGE.md).

---

## 13. License and source code

Spledio is free software, licensed under the **GNU General Public License, version 3 only
(GPL-3.0-only)**. You may use it for any purpose, study and change the source code, and share it,
including modified versions, under the same license. It comes **without any warranty**.

* Source code: <https://github.com/iacompa/Spledio>
* Full license: [LICENSE](../LICENSE)
* Third-party components (Qt/PySide6 under LGPL-3.0, NumPy, SciPy, libsndfile, PortAudio and others):
  [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). In the app, **Help → Licenses and Notices** shows
  Spledio's license and every third-party license text. Nothing is downloaded: the files are inside the app.
* The source code of the libraries under the LGPL, and how to run Spledio with your own build of one of
  them: [licenses/SOURCE-AND-REPLACING-LIBRARIES.md](../licenses/SOURCE-AND-REPLACING-LIBRARIES.md).

Spledio is an independent project. It is not affiliated with or endorsed by any maker of commercial audio software.

## 14. Help and bug reports

Report bugs or ask questions on [GitHub Issues](https://github.com/iacompa/Spledio/issues).
Please include your Spledio version (**Help → About**), your operating system and the steps that cause the problem.
