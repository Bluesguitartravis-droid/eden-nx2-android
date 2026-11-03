// SPDX-FileCopyrightText: Copyright 2026 Eden NX2 Android contributors
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Plaintext NSP container writer adapted from NSP Box v3.0.0 (MIT).
// Copyright (c) 2026 Switch 2 XCI Packager contributors.
// See assets/licenses/NSP-Box-LICENSE.txt.

package org.yuzu.yuzu_emu.features.packer

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.DocumentsContract
import android.view.Gravity
import android.view.View
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.documentfile.provider.DocumentFile
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.google.android.material.progressindicator.LinearProgressIndicator
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.util.Locale
import java.util.concurrent.CancellationException
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.min
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.yuzu.yuzu_emu.R

/**
 * Packages a complete, already-extracted ExeFS + RomFS snapshot into an unsigned plaintext NSP.
 *
 * This intentionally does not decrypt NSP/XCI content, handle tickets/keys, reconstruct retail
 * authentication, or download content. The generated package targets compatible emulators only.
 */
class SnapshotPackerActivity : AppCompatActivity() {
    private lateinit var sourceButton: MaterialButton
    private lateinit var outputButton: MaterialButton
    private lateinit var cancelButton: MaterialButton
    private lateinit var sourceText: TextView
    private lateinit var statusText: TextView
    private lateinit var titleInput: EditText
    private lateinit var progress: LinearProgressIndicator

    private var snapshot: Snapshot? = null
    private var packJob: Job? = null
    private val cancelled = AtomicBoolean(false)

    private val pickSource = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri == null) return@registerForActivityResult
        try {
            contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        } catch (_: SecurityException) {
            // Some providers grant access for the current activity without persistable permission.
        }
        scanSource(uri)
    }

    private val pickOutputFolder = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri == null) return@registerForActivityResult
        try {
            contentResolver.takePersistableUriPermission(
                uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            )
        } catch (_: SecurityException) {
        }
        startPackaging(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = getString(R.string.snapshot_packer)
        setContentView(buildUi())

        sourceButton.setOnClickListener { pickSource.launch(null) }
        outputButton.setOnClickListener { pickOutputFolder.launch(null) }
        cancelButton.setOnClickListener {
            cancelled.set(true)
            statusText.text = getString(R.string.snapshot_packer_cancelling)
        }
    }

    override fun onDestroy() {
        cancelled.set(true)
        packJob?.cancel()
        super.onDestroy()
    }

    private fun buildUi(): View {
        val padding = dp(20)
        val spacing = dp(12)
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(padding, padding, padding, padding)
        }

        content.addView(TextView(this).apply {
            text = getString(R.string.snapshot_packer)
            textSize = 24f
        }, linearParams(-1, -2, bottom = spacing))

        content.addView(TextView(this).apply {
            text = getString(R.string.snapshot_packer_intro)
            textSize = 15f
        }, linearParams(-1, -2, bottom = spacing))

        sourceButton = MaterialButton(this).apply {
            text = getString(R.string.snapshot_packer_choose_source)
        }
        content.addView(sourceButton, linearParams(-1, -2, bottom = spacing))

        sourceText = TextView(this).apply {
            text = getString(R.string.snapshot_packer_no_source)
        }
        content.addView(sourceText, linearParams(-1, -2, bottom = spacing))

        titleInput = EditText(this).apply {
            hint = getString(R.string.snapshot_packer_title_hint)
            isSingleLine = true
            isEnabled = false
        }
        content.addView(titleInput, linearParams(-1, -2, bottom = spacing))

        outputButton = MaterialButton(this).apply {
            text = getString(R.string.snapshot_packer_choose_output)
            isEnabled = false
        }
        content.addView(outputButton, linearParams(-1, -2, bottom = spacing))

        progress = LinearProgressIndicator(this).apply {
            isIndeterminate = false
            max = 100
            progress = 0
            visibility = View.GONE
        }
        content.addView(progress, linearParams(-1, dp(6), bottom = spacing))

        statusText = TextView(this).apply {
            text = getString(R.string.snapshot_packer_idle)
        }
        content.addView(statusText, linearParams(-1, -2, bottom = spacing))

        cancelButton = MaterialButton(this).apply {
            text = getString(android.R.string.cancel)
            visibility = View.GONE
        }
        content.addView(cancelButton, linearParams(-1, -2, bottom = spacing))

        content.addView(TextView(this).apply {
            text = getString(R.string.snapshot_packer_limitations)
            textSize = 13f
            alpha = 0.75f
        }, linearParams(-1, -2, bottom = spacing))

        return ScrollView(this).apply {
            addView(content)
        }
    }

    private fun linearParams(width: Int, height: Int, bottom: Int = 0): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(width, height).apply {
            bottomMargin = bottom
            gravity = Gravity.CENTER_HORIZONTAL
        }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun scanSource(uri: Uri) {
        sourceButton.isEnabled = false
        outputButton.isEnabled = false
        titleInput.isEnabled = false
        progress.visibility = View.VISIBLE
        progress.isIndeterminate = true
        statusText.text = getString(R.string.snapshot_packer_scanning)
        cancelled.set(false)

        lifecycleScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    val root = DocumentFile.fromTreeUri(this@SnapshotPackerActivity, uri)
                        ?: error("Unable to open the selected folder")
                    SnapshotScanner(contentResolver::openInputStream).findBest(root)
                }
            }
            sourceButton.isEnabled = true
            progress.isIndeterminate = false
            progress.visibility = View.GONE

            result.onSuccess { found ->
                snapshot = found
                titleInput.setText(found.title)
                titleInput.isEnabled = true
                outputButton.isEnabled = true
                sourceText.text = getString(
                    R.string.snapshot_packer_source_ready,
                    if (found.baseRomfs != null) found.label + " (base + update RomFS)" else found.label,
                    String.format(Locale.US, "%016X", found.titleId),
                    found.version
                )
                statusText.text = getString(R.string.snapshot_packer_ready)
            }.onFailure { error ->
                snapshot = null
                sourceText.text = getString(R.string.snapshot_packer_no_source)
                statusText.text = error.message ?: getString(R.string.snapshot_packer_scan_failed)
            }
        }
    }

    private fun startPackaging(outputTree: Uri) {
        val selected = snapshot ?: return
        if (isSameOrDescendantTree(outputTree, selected.root.uri)) {
            Toast.makeText(
                this,
                "Choose an output folder outside the extracted game snapshot.",
                Toast.LENGTH_LONG
            ).show()
            return
        }
        val title = titleInput.text?.toString()?.trim().orEmpty().ifEmpty { selected.title }

        sourceButton.isEnabled = false
        outputButton.isEnabled = false
        titleInput.isEnabled = false
        cancelButton.visibility = View.VISIBLE
        progress.visibility = View.VISIBLE
        progress.isIndeterminate = false
        progress.progress = 0
        cancelled.set(false)
        statusText.text = getString(R.string.snapshot_packer_preparing)

        packJob = lifecycleScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) {
                    val outRoot = DocumentFile.fromTreeUri(this@SnapshotPackerActivity, outputTree)
                        ?: error("Unable to open the output folder")
                    val packer = PlaintextNspPacker(
                        resolver = contentResolver::openInputStream,
                        outputResolver = { uri -> contentResolver.openOutputStream(uri, "w") },
                        cancelled = { cancelled.get() },
                        progress = { percent, message ->
                            runOnUiThread {
                                this@SnapshotPackerActivity.progress.progress = percent.coerceIn(0, 100)
                                statusText.text = message
                            }
                        }
                    )
                    packer.pack(selected, outRoot, title)
                }
            }

            sourceButton.isEnabled = true
            outputButton.isEnabled = snapshot != null
            titleInput.isEnabled = snapshot != null
            cancelButton.visibility = View.GONE
            progress.visibility = View.GONE

            result.onSuccess { output ->
                statusText.text = getString(R.string.snapshot_packer_complete, output)
                MaterialAlertDialogBuilder(this@SnapshotPackerActivity)
                    .setTitle(R.string.snapshot_packer_complete_title)
                    .setMessage(getString(R.string.snapshot_packer_complete_message, output))
                    .setPositiveButton(android.R.string.ok, null)
                    .show()
            }.onFailure { error ->
                val message = if (error is CancellationException || cancelled.get()) {
                    getString(R.string.snapshot_packer_cancelled)
                } else {
                    error.message ?: getString(R.string.snapshot_packer_failed)
                }
                statusText.text = message
                if (error !is CancellationException && !cancelled.get()) {
                    Toast.makeText(this@SnapshotPackerActivity, message, Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    private fun isSameOrDescendantTree(candidateTree: Uri, sourceDocument: Uri): Boolean {
        if (candidateTree.authority != sourceDocument.authority) return false
        return try {
            val candidateId = DocumentsContract.getTreeDocumentId(candidateTree)
            val sourceId = DocumentsContract.getDocumentId(sourceDocument)
            candidateId == sourceId || candidateId.startsWith("$sourceId/")
        } catch (_: IllegalArgumentException) {
            false
        }
    }

    data class Snapshot(
        val root: DocumentFile,
        val exefs: DocumentFile,
        val romfs: DocumentFile,
        val mainNpdm: DocumentFile,
        val mainNpdmBytes: ByteArray,
        val controlNacpBytes: ByteArray?,
        val titleId: Long,
        val version: Int,
        val label: String,
        val title: String,
        // Optional base RomFS to merge under an extracted update RomFS.
        // Most update snapshots contain only changed files.
        val baseRomfs: DocumentFile? = null
    )

    private class SnapshotScanner(private val openInput: (Uri) -> InputStream?) {
        fun findBest(root: DocumentFile): Snapshot {
            val candidates = mutableListOf<Snapshot>()
            val invalid = mutableListOf<String>()
            val rootLabel = root.name ?: "Selected folder"
            val rootTitle = cleanTitle(rootLabel)
            val rootVersion = root.name?.toIntOrNull()?.takeIf { it >= 0 } ?: 0
            scan(root, rootLabel, 0, rootVersion, rootTitle, candidates, invalid)
            // DKB can have an apparently complete base/ snapshot whose RomFS only
            // contains Voice/Voxel/XLink. Packaging that as an NSP loses the updated
            // System/HeapSize and RSDB data, and the game later initializes a heap
            // through a null pointer. Filter incomplete candidates before selecting
            // the highest version, without changing validation for other titles.
            // Pair an extracted updates/<version> snapshot with the base/
            // snapshot from the same selected tree and same title ID.
            // This mirrors the update-over-base behavior of Eglantine's NUP
            // loader, while keeping the existing .nsp output format.
            val composedCandidates = candidates.map { candidate ->
                val isUpdate = candidate.label.contains("/updates/", ignoreCase = true) ||
                    candidate.label.contains("/update/", ignoreCase = true)
                val base = if (isUpdate) {
                    candidates.firstOrNull { other ->
                        other !== candidate &&
                            other.titleId == candidate.titleId &&
                            other.label.substringAfterLast('/').equals("base", ignoreCase = true)
                    }
                } else {
                    null
                }
                if (base != null) candidate.copy(baseRomfs = base.romfs) else candidate
            }

            val launchReady = composedCandidates.filter { candidate ->
                if (candidate.titleId != 0x040074A01BF12000L) {
                    true
                } else {
                    val updateSystem = candidate.romfs.findFile("System")
                    val baseSystem = candidate.baseRomfs?.findFile("System")
                    val heapSize = updateSystem?.takeIf { it.isDirectory }?.findFile("HeapSize")
                        ?: baseSystem?.takeIf { it.isDirectory }?.findFile("HeapSize")
                    val rsdb = sequenceOf(candidate.romfs, candidate.baseRomfs)
                        .filterNotNull()
                        .any { it.findFile("RSDB")?.isDirectory == true }
                    val ready = heapSize != null && rsdb
                    if (!ready) {
                        invalid += "${candidate.label}: DKB effective RomFS is missing System/HeapSize " +
                            "or RSDB. The app now merges base + update when both are available, " +
                            "but this snapshot still lacks required resources."
                    }
                    ready
                }
            }

            if (launchReady.isEmpty()) {
                val detail = invalid.firstOrNull()?.let { "\n$it" }.orEmpty()
                error(
                    "No launch-ready snapshot found. Choose a folder containing exefs/main, " +
                        "exefs/main.npdm and a complete romfs/." + detail
                )
            }
            return launchReady.maxWith(compareBy<Snapshot> { it.version }.thenBy { it.label })
        }

        private fun scan(
            node: DocumentFile,
            label: String,
            depth: Int,
            inheritedVersion: Int,
            rootTitle: String,
            out: MutableList<Snapshot>,
            invalid: MutableList<String>
        ) {
            if (depth > 4 || !node.isDirectory) return
            val exefs = node.findFile("exefs")
            val romfs = node.findFile("romfs")
            val main = exefs?.findFile("main")
            val npdm = exefs?.findFile("main.npdm")
            val nodeVersion = node.name?.toIntOrNull()?.takeIf { it >= 0 } ?: inheritedVersion

            if (
                exefs?.isDirectory == true &&
                romfs?.isDirectory == true &&
                main?.isFile == true &&
                npdm?.isFile == true
            ) {
                try {
                    val npdmBytes = openInput(npdm.uri)?.use { input ->
                        val data = input.readBytes()
                        if (data.size > 4 * 1024 * 1024) {
                            error("main.npdm is unexpectedly large")
                        }
                        data
                    } ?: error("Unable to read main.npdm")
                    val titleId = readTitleId(npdmBytes)

                    // A title ID with the patch bit is not a standalone application snapshot.
                    if ((titleId and 0x800L) != 0L) {
                        error("NPDM identifies a patch, not a main application")
                    }

                    val controlFile = sequenceOf(
                        node.findFile("control.nacp"),
                        node.findFile("control")?.findFile("control.nacp")
                    ).filterNotNull().firstOrNull { it.isFile && it.length() == 0x4000L }
                    val controlBytes = controlFile?.let { file ->
                        openInput(file.uri)?.use { input -> input.readBytes() }
                            ?.takeIf { it.size == 0x4000 }
                    }

                    out += Snapshot(
                        node,
                        exefs,
                        romfs,
                        npdm,
                        npdmBytes,
                        controlBytes,
                        titleId,
                        nodeVersion,
                        label,
                        rootTitle
                    )
                } catch (error: Exception) {
                    invalid += "$label: ${error.message ?: error::class.java.simpleName}"
                }
            }

            if (depth == 4) return
            node.listFiles()
                .filter {
                    it.isDirectory &&
                        it.name?.lowercase(Locale.ROOT) !in setOf(
                            "exefs", "romfs", "control", "dlc", "aoc",
                            "addons", "add_on_content"
                        )
                }
                .sortedBy { it.name?.lowercase(Locale.ROOT) }
                .forEach { child ->
                    val childVersion =
                        child.name?.toIntOrNull()?.takeIf { it >= 0 } ?: nodeVersion
                    scan(
                        child,
                        "$label/${child.name ?: "folder"}",
                        depth + 1,
                        childVersion,
                        rootTitle,
                        out,
                        invalid
                    )
                }
        }

        private fun readTitleId(data: ByteArray): Long {
            require(
                data.size >= 0x80 &&
                    data.copyOfRange(0, 4).contentEquals("META".toByteArray())
            ) {
                "main.npdm is not a supported NPDM file"
            }
            val offset = u32(data, 0x70)
            val size = u32(data, 0x74)
            require(offset >= 0x80 && size >= 0x20 && offset + size <= data.size) {
                "The NPDM application metadata is incomplete"
            }
            require(
                data.copyOfRange(offset, offset + 4).contentEquals("ACI0".toByteArray())
            ) {
                "The NPDM ACI0 section is missing"
            }
            val titleId = u64(data, offset + 0x10)
            require(titleId != 0L) { "The NPDM has no application title ID" }
            return titleId
        }

        private fun cleanTitle(name: String): String =
            name.replace(Regex("""\s*\[[^]]*]"""), "")
                .trim()
                .ifEmpty { "Extracted game" }

        private fun u32(data: ByteArray, at: Int): Int =
            ByteBuffer.wrap(data, at, 4).order(ByteOrder.LITTLE_ENDIAN).int

        private fun u64(data: ByteArray, at: Int): Long =
            ByteBuffer.wrap(data, at, 8).order(ByteOrder.LITTLE_ENDIAN).long
    }

    private class PlaintextNspPacker(
        private val resolver: (Uri) -> InputStream?,
        private val outputResolver: (Uri) -> OutputStream?,
        private val cancelled: () -> Boolean,
        private val progress: (Int, String) -> Unit
    ) {
        private val maxPackageSize = 32L shl 30

        fun pack(snapshot: Snapshot, outputRoot: DocumentFile, title: String): String {
            checkpoint()
            require(hasAnyFile(snapshot.romfs)) { "The RomFS folder is empty." }
            val safeTitle = sanitize(title)
            val titleIdHex = String.format(Locale.US, "%016X", snapshot.titleId)
            val folderName = "$safeTitle [$titleIdHex]"
            require(outputRoot.findFile(folderName) == null) {
                "An output folder named '$folderName' already exists"
            }
            val packageDir = outputRoot.createDirectory(folderName)
                ?: error("Unable to create output folder")
            var nsp: DocumentFile? = null
            var companion: DocumentFile? = null
            try {
                progress(2, "Reading extracted snapshot…")
                val exefs = pfsFromDirectory(snapshot.exefs)
                progress(
                    5,
                    if (snapshot.baseRomfs != null) "Merging base and update RomFS…"
                    else "Building RomFS file tables…"
                )
                val romfs = romfsFromDirectory(snapshot.romfs, snapshot.baseRomfs)

                progress(8, "Building integrity trees…")
                val program = nca(
                    listOf(pfsSection(exefs), romfsSection(romfs)),
                    snapshot.titleId,
                    contentType = 0,
                    distribution = 0
                )

                val nacp = controlData(snapshot, title)
                val controlRomfs = romfsFromEntries(listOf("control.nacp" to Blob.bytes(nacp)))
                val control = nca(
                    listOf(romfsSection(controlRomfs)),
                    snapshot.titleId,
                    contentType = 2,
                    distribution = 0
                )

                progress(42, "Hashing Program and Control content…")
                val programDigest = program.digest(cancelled)
                val controlDigest = control.digest(cancelled)
                val programName = programDigest.copyOfRange(0, 16).hex() + ".nca"
                val controlName = controlDigest.copyOfRange(0, 16).hex() + ".nca"

                val records = listOf(
                    contentRecord(programDigest, program.size, 1),
                    contentRecord(controlDigest, control.size, 3)
                )
                val cnmtBody = ByteArrayOutputStream().apply {
                    write(ByteArray(0x20).also { header ->
                        putLong(header, 0, snapshot.titleId)
                        putInt(header, 8, snapshot.version)
                        header[12] = 0x80.toByte()
                        header[13] = 0
                        putShort(header, 14, 16)
                        putShort(header, 16, records.size)
                        putShort(header, 18, 0)
                    })
                    write(leBytes(16).also { ext ->
                        putLong(ext, 0, snapshot.titleId + 0x800)
                        putInt(ext, 8, 0)
                        putInt(ext, 12, 0)
                    })
                    records.forEach { write(it) }
                }.toByteArray()
                val cnmt = cnmtBody + sha256(cnmtBody)
                val cnmtName = "Application_${titleIdHex.lowercase(Locale.ROOT)}.cnmt"
                val metaPfs = filesystem(listOf(cnmtName to Blob.bytes(cnmt)))
                val meta = nca(
                    listOf(pfsSection(metaPfs)),
                    snapshot.titleId,
                    contentType = 1,
                    distribution = 0
                )
                val metaDigest = meta.digest(cancelled)
                val metaName = metaDigest.copyOfRange(0, 16).hex() + ".cnmt.nca"

                progress(60, "Preparing NSP container…")
                val packageBlob = filesystem(
                    listOf(
                        programName to program,
                        controlName to control,
                        metaName to meta
                    )
                )
                require(packageBlob.size <= maxPackageSize) {
                    "The generated NSP would exceed the 32 GiB experimental limit"
                }

                nsp = packageDir.createFile("application/octet-stream", "$safeTitle.nsp")
                    ?: error("Unable to create NSP output")
                val out = outputResolver(nsp.uri) ?: error("Unable to open NSP output")
                out.use { stream ->
                    var written = 0L
                    packageBlob.writeTo(stream, cancelled) { count ->
                        written += count
                        val p = 60 + ((written * 38L) / packageBlob.size.coerceAtLeast(1L)).toInt()
                        progress(p.coerceAtMost(98), "Writing NSP… ${written / (1024 * 1024)} MiB")
                    }
                    stream.flush()
                }

                checkpoint()
                companion = packageDir.createFile("application/octet-stream", "main.npdm")
                    ?: error("Unable to create main.npdm companion")
                outputResolver(companion.uri)?.use { it.write(snapshot.mainNpdmBytes) }
                    ?: error("Unable to write main.npdm companion")

                progress(100, "Complete")
                return "$folderName/$safeTitle.nsp"
            } catch (e: Throwable) {
                nsp?.delete()
                companion?.delete()
                if (packageDir.listFiles().isEmpty()) packageDir.delete()
                throw e
            }
        }

        private fun checkpoint() {
            if (cancelled()) throw CancellationException("Packaging cancelled")
        }

        private fun hasAnyFile(dir: DocumentFile): Boolean {
            checkpoint()
            for (child in dir.listFiles()) {
                if (child.isFile) return true
                if (child.isDirectory && hasAnyFile(child)) return true
            }
            return false
        }

        private fun sanitize(text: String): String = text
            .replace(Regex("""[\\/:*?"<>|]"""), "_")
            .trim()
            .take(96)
            .ifEmpty { "Extracted game" }

        private fun pfsFromDirectory(dir: DocumentFile): Blob {
            val entries = dir.listFiles().sortedBy { it.name ?: "" }.map { file ->
                require(file.isFile) { "ExeFS must contain files directly, not nested folders" }
                (file.name ?: error("ExeFS contains an unnamed file")) to Blob.document(file.uri, file.length(), resolver)
            }
            return filesystem(entries)
        }

        private fun controlData(snapshot: Snapshot, title: String): ByteArray {
            val existing = snapshot.controlNacpBytes
            val nacp = existing?.copyOf() ?: ByteArray(0x4000)

            // Preserve compressed/localized control metadata. For ordinary uncompressed metadata,
            // update only the visible title/version fields just like NSP Box.
            if (existing == null || nacp[0x3215].toInt() == 0) {
                repeat(16) { index ->
                    putUtf8(nacp, index * 0x300, 0x200, title)
                }
            }

            if (existing == null) {
                nacp[0x3025] = 1
                putInt(nacp, 0x302c, 0xffff)
                putLong(nacp, 0x3038, snapshot.titleId)
                putLong(nacp, 0x3070, snapshot.titleId + 0x1000)
                putLong(nacp, 0x3078, snapshot.titleId)
                if ((snapshot.titleId ushr 56) == 4L) {
                    nacp[0x3214] = 3
                    nacp[0x3217] = 1
                }
            }

            // The display version field is always safe to refresh.
            nacp.fill(0, 0x3060, 0x3070)
            putUtf8(
                nacp,
                0x3060,
                0x10,
                if (snapshot.version == 0) "1.0.0" else snapshot.version.toString()
            )
            return nacp
        }

        private fun contentRecord(digest: ByteArray, size: Long, contentType: Int): ByteArray {
            val out = ByteArray(0x38)
            digest.copyInto(out, 0, 0, 32)
            digest.copyInto(out, 32, 0, 16)
            var value = size
            repeat(6) { i ->
                out[48 + i] = (value and 0xff).toByte()
                value = value ushr 8
            }
            out[54] = contentType.toByte()
            out[55] = 0
            return out
        }

        private fun filesystem(entries: List<Pair<String, Blob>>): Blob {
            val names = entries.map { it.first.toByteArray(Charsets.UTF_8) }
            val rawNameBytes = names.sumOf { it.size + 1 }
            val headerBytes = 0x10 + entries.size * 0x18
            val tableSize = align(headerBytes + rawNameBytes.toLong(), 0x10).toInt() - headerBytes
            val stringTable = ByteArray(tableSize)
            var namePos = 0
            names.forEach { name ->
                name.copyInto(stringTable, namePos)
                namePos += name.size + 1
            }

            val header = ByteArray(headerBytes)
            "PFS0".toByteArray().copyInto(header, 0)
            putInt(header, 4, entries.size)
            putInt(header, 8, tableSize)
            putInt(header, 12, 0)

            var dataOffset = 0L
            var stringOffset = 0
            entries.forEachIndexed { index, (name, blob) ->
                val at = 0x10 + index * 0x18
                putLong(header, at, dataOffset)
                putLong(header, at + 8, blob.size)
                putInt(header, at + 16, stringOffset)
                putInt(header, at + 20, 0)
                stringOffset += name.toByteArray(Charsets.UTF_8).size + 1
                dataOffset += align(blob.size, 0x10)
            }

            val parts = mutableListOf<Part>(Part.Bytes(header), Part.Bytes(stringTable))
            entries.forEach { (_, blob) ->
                parts += blob.parts
                val pad = align(blob.size, 0x10) - blob.size
                if (pad > 0) parts += Part.Zero(pad)
            }
            return Blob(parts)
        }

        private fun pfsSection(blob: Blob): Section {
            var block = 0x10000
            while (blob.size > (block.toLong() * block.toLong()) / 32L) block *= 2
            val hashes = ByteArrayOutputStream()
            blob.forEachBlock(block, cancelled, padLast = false) { bytes, length ->
                hashes.write(sha256(bytes, length))
            }
            val hashBytes = hashes.toByteArray()
            val dataOffset = align(hashBytes.size.toLong(), 0x200)
            val header = ByteArray(0x200)
            putShort(header, 0, 2)
            header[2] = 1
            header[3] = 2
            header[4] = 1
            sha256(hashBytes).copyInto(header, 8)
            putInt(header, 0x28, block)
            putInt(header, 0x2c, 2)
            putLong(header, 0x30, 0)
            putLong(header, 0x38, hashBytes.size.toLong())
            putLong(header, 0x40, dataOffset)
            putLong(header, 0x48, blob.size)
            val body = Blob(
                mutableListOf<Part>(Part.Bytes(hashBytes)).apply {
                    if (dataOffset > hashBytes.size) add(Part.Zero(dataOffset - hashBytes.size))
                    addAll(blob.parts)
                }
            ).padded(0x200)
            return Section(header, body)
        }

        private fun romfsSection(blob: Blob): Section {
            val block = 0x4000
            val levels = mutableListOf(blob)
            repeat(5) {
                val hashes = ByteArrayOutputStream()
                levels.first().forEachBlock(block, cancelled, padLast = true) { bytes, length ->
                    val padded = if (length == block) bytes else bytes.copyOf(block)
                    hashes.write(sha256(padded))
                }
                levels.add(0, Blob.bytes(hashes.toByteArray()))
            }
            val firstBlock = levels.first().readHead(block, cancelled).copyOf(block)
            val master = sha256(firstBlock)
            val header = ByteArray(0x200)
            putShort(header, 0, 2)
            header[2] = 0
            header[3] = 3
            header[4] = 1
            "IVFC".toByteArray().copyInto(header, 8)
            putInt(header, 12, 0x20000)
            putInt(header, 16, 32)
            putInt(header, 20, 7)
            var offset = 0L
            val bodyParts = mutableListOf<Part>()
            levels.forEachIndexed { index, level ->
                val at = 0x18 + 24 * index
                putLong(header, at, offset)
                putLong(header, at + 8, level.size)
                putInt(header, at + 16, 14)
                putInt(header, at + 20, 0)
                val padded = level.padded(block)
                bodyParts += padded.parts
                offset += padded.size
            }
            master.copyInto(header, 0xc8)
            return Section(header, Blob(bodyParts))
        }

        private fun nca(sections: List<Section>, titleId: Long, contentType: Int, distribution: Int): Blob {
            val header = ByteArray(0xc00)
            "NCA3".toByteArray().copyInto(header, 0x200)
            header[0x204] = distribution.toByte()
            header[0x205] = contentType.toByte()
            header[0x206] = 1
            header[0x207] = 0
            putLong(header, 0x210, titleId)
            putInt(header, 0x21c, 0x000c1100)
            val parts = mutableListOf<Part>(Part.Bytes(header))
            var offset = 0xc00L
            sections.forEachIndexed { index, section ->
                require(section.header.size == 0x200)
                val body = section.body.padded(0x200)
                val at = 0x240 + 16 * index
                putInt(header, at, (offset / 512).toInt())
                putInt(header, at + 4, ((offset + body.size) / 512).toInt())
                putInt(header, at + 8, 0)
                putInt(header, at + 12, 0)
                sha256(section.header).copyInto(header, 0x280 + 32 * index)
                section.header.copyInto(header, 0x400 + 512 * index)
                parts += body.parts
                offset += body.size
            }
            putLong(header, 0x208, offset)
            return Blob(parts)
        }

        private fun romfsFromDirectory(root: DocumentFile, base: DocumentFile? = null): Blob {
            val rootDir = DirNode("", null)
            // Overlay semantics: updates replace files by name while base-only
            // files and nested directories remain reachable.
            if (base != null) {
                buildDocumentTree(base, rootDir)
            }
            buildDocumentTree(root, rootDir)
            return buildRomfs(rootDir)
        }

        private fun romfsFromEntries(entries: List<Pair<String, Blob>>): Blob {
            val root = DirNode("", null)
            entries.sortedBy { it.first }.forEach { (name, blob) ->
                root.files += FileNode(name, root, blob)
            }
            return buildRomfs(root)
        }

        private fun buildDocumentTree(source: DocumentFile, target: DirNode) {
            checkpoint()
            // Index once per directory so large (200k+ file) snapshots don't
            // fall back to quadratic work while applying update overrides.
            val dirsByName = target.dirs.associateBy { it.name }.toMutableMap()
            val filePositions = target.files.withIndex()
                .associate { (index, file) -> file.name to index }.toMutableMap()
            source.listFiles().sortedWith { a, b -> utf8Compare(a.name ?: "", b.name ?: "") }.forEach { child ->
                val name = child.name ?: error("RomFS contains an unnamed entry")
                if (child.isDirectory) {
                    // A directory shadows a base file of the same name.
                    if (filePositions.remove(name) != null) {
                        target.files.removeAll { it.name == name }
                        filePositions.clear()
                        target.files.forEachIndexed { index, file -> filePositions[file.name] = index }
                    }
                    val dir = dirsByName[name]
                        ?: DirNode(name, target).also {
                            target.dirs += it
                            dirsByName[name] = it
                        }
                    buildDocumentTree(child, dir)
                } else if (child.isFile) {
                    // Update files replace their base names in-place; base
                    // siblings remain untouched.
                    if (dirsByName.remove(name) != null) {
                        target.dirs.removeAll { it.name == name }
                    }
                    val replacement = FileNode(
                        name, target, Blob.document(child.uri, child.length(), resolver)
                    )
                    val existing = filePositions[name]
                    if (existing != null) {
                        target.files[existing] = replacement
                    } else {
                        filePositions[name] = target.files.size
                        target.files += replacement
                    }
                }
            }
        }

        private fun buildRomfs(root: DirNode): Blob {
            val dirs = mutableListOf<DirNode>()
            val files = mutableListOf<FileNode>()
            fun collect(dir: DirNode) {
                dirs += dir
                dir.dirs.forEach(::collect)
                files += dir.files
            }
            collect(root)

            var dirOff = 0
            dirs.forEach { dir ->
                dir.offset = dirOff
                dirOff += 0x18 + align(dir.nameBytes.size.toLong(), 4).toInt()
            }
            var fileOff = 0
            var dataOff = 0L
            files.forEach { file ->
                file.offset = fileOff
                fileOff += 0x20 + align(file.nameBytes.size.toLong(), 4).toInt()
                file.dataOffset = align(dataOff, 0x10)
                dataOff = file.dataOffset + file.blob.size
            }
            dirs.forEach { dir ->
                dir.dirs.forEachIndexed { i, child -> child.sibling = dir.dirs.getOrNull(i + 1)?.offset ?: -1 }
                dir.files.forEachIndexed { i, child -> child.sibling = dir.files.getOrNull(i + 1)?.offset ?: -1 }
            }

            val dirBuckets = IntArray(bucketCount(dirs.size)) { -1 }
            val fileBuckets = IntArray(bucketCount(files.size)) { -1 }
            val dirTable = ByteArray(dirOff)
            val fileTable = ByteArray(fileOff)

            dirs.forEach { dir ->
                val parentOffset = dir.parent?.offset ?: 0
                val bucket = (nameHash(parentOffset, dir.nameBytes).toUInt() % dirBuckets.size.toUInt()).toInt()
                val at = dir.offset
                putInt(dirTable, at, parentOffset)
                putInt(dirTable, at + 4, dir.sibling)
                putInt(dirTable, at + 8, dir.dirs.firstOrNull()?.offset ?: -1)
                putInt(dirTable, at + 12, dir.files.firstOrNull()?.offset ?: -1)
                putInt(dirTable, at + 16, dirBuckets[bucket])
                putInt(dirTable, at + 20, dir.nameBytes.size)
                dir.nameBytes.copyInto(dirTable, at + 0x18)
                dirBuckets[bucket] = dir.offset
            }

            files.forEach { file ->
                val parentOffset = file.parent.offset
                val bucket = (nameHash(parentOffset, file.nameBytes).toUInt() % fileBuckets.size.toUInt()).toInt()
                val at = file.offset
                putInt(fileTable, at, parentOffset)
                putInt(fileTable, at + 4, file.sibling)
                putLong(fileTable, at + 8, file.dataOffset)
                putLong(fileTable, at + 16, file.blob.size)
                putInt(fileTable, at + 24, fileBuckets[bucket])
                putInt(fileTable, at + 28, file.nameBytes.size)
                file.nameBytes.copyInto(fileTable, at + 0x20)
                fileBuckets[bucket] = file.offset
            }

            val dirHash = ByteArray(dirBuckets.size * 4).also { bytes ->
                dirBuckets.forEachIndexed { i, value -> putInt(bytes, i * 4, value) }
            }
            val fileHash = ByteArray(fileBuckets.size * 4).also { bytes ->
                fileBuckets.forEachIndexed { i, value -> putInt(bytes, i * 4, value) }
            }

            val dirHashPos = 0x50L
            val dirTablePos = dirHashPos + dirHash.size
            val fileHashPos = dirTablePos + dirTable.size
            val fileTablePos = fileHashPos + fileHash.size
            val dataPos = align(fileTablePos + fileTable.size, 0x10)
            val header = ByteArray(0x50)
            val values = longArrayOf(
                0x50L,
                dirHashPos, dirHash.size.toLong(),
                dirTablePos, dirTable.size.toLong(),
                fileHashPos, fileHash.size.toLong(),
                fileTablePos, fileTable.size.toLong(),
                dataPos
            )
            values.forEachIndexed { i, value -> putLong(header, i * 8, value) }

            val parts = mutableListOf<Part>(
                Part.Bytes(header), Part.Bytes(dirHash), Part.Bytes(dirTable),
                Part.Bytes(fileHash), Part.Bytes(fileTable)
            )
            val metaEnd = fileTablePos + fileTable.size
            if (dataPos > metaEnd) parts += Part.Zero(dataPos - metaEnd)
            var current = 0L
            files.forEach { file ->
                if (file.dataOffset > current) parts += Part.Zero(file.dataOffset - current)
                parts += file.blob.parts
                current = file.dataOffset + file.blob.size
            }
            return Blob(parts)
        }

        private fun bucketCount(count: Int): Int {
            if (count < 3) return 3
            var n = if (count < 19) count or 1 else count
            while (n % 2 == 0 || n % 3 == 0 || n % 5 == 0 || n % 7 == 0 || n % 11 == 0 || n % 13 == 0 || n % 17 == 0) n++
            return n
        }

        private fun nameHash(parent: Int, name: ByteArray): Int {
            var hash = parent
            hash = hash xor 123456789
            for (byte in name) {
                val c = byte.toInt() and 0xff
                hash = ((hash ushr 5) or (hash shl 27)) xor c
            }
            return hash
        }

        private fun utf8Compare(a: String, b: String): Int {
            val aa = a.toByteArray(Charsets.UTF_8)
            val bb = b.toByteArray(Charsets.UTF_8)
            val limit = min(aa.size, bb.size)
            for (i in 0 until limit) {
                val x = aa[i].toInt() and 0xff
                val y = bb[i].toInt() and 0xff
                if (x != y) return x - y
            }
            return aa.size - bb.size
        }

        private fun putUtf8(buffer: ByteArray, offset: Int, size: Int, text: String) {
            val bytes = text.toByteArray(Charsets.UTF_8)
            val count = min(bytes.size, size - 1)
            bytes.copyInto(buffer, offset, 0, count)
        }

        private fun sha256(data: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(data)

        private fun sha256(data: ByteArray, length: Int): ByteArray {
            val md = MessageDigest.getInstance("SHA-256")
            md.update(data, 0, length)
            return md.digest()
        }

        private fun align(value: Long, alignment: Int): Long =
            ((value + alignment - 1L) / alignment) * alignment

        private fun leBytes(size: Int) = ByteArray(size)

        private fun putShort(data: ByteArray, at: Int, value: Int) {
            ByteBuffer.wrap(data, at, 2).order(ByteOrder.LITTLE_ENDIAN).putShort(value.toShort())
        }

        private fun putInt(data: ByteArray, at: Int, value: Int) {
            ByteBuffer.wrap(data, at, 4).order(ByteOrder.LITTLE_ENDIAN).putInt(value)
        }

        private fun putLong(data: ByteArray, at: Int, value: Long) {
            ByteBuffer.wrap(data, at, 8).order(ByteOrder.LITTLE_ENDIAN).putLong(value)
        }

        private fun ByteArray.hex(): String = joinToString("") { "%02x".format(it.toInt() and 0xff) }

        private data class Section(val header: ByteArray, val body: Blob)

        private class DirNode(val name: String, val parent: DirNode?) {
            val nameBytes = name.toByteArray(Charsets.UTF_8)
            val dirs = mutableListOf<DirNode>()
            val files = mutableListOf<FileNode>()
            var offset = 0
            var sibling = -1
        }

        private class FileNode(val name: String, val parent: DirNode, val blob: Blob) {
            val nameBytes = name.toByteArray(Charsets.UTF_8)
            var offset = 0
            var sibling = -1
            var dataOffset = 0L
        }

        private sealed class Part {
            data class Bytes(val data: ByteArray) : Part()
            data class Zero(val count: Long) : Part()
            data class Document(
                val uri: Uri,
                val length: Long,
                val opener: (Uri) -> InputStream?
            ) : Part()

            val size: Long
                get() = when (this) {
                    is Bytes -> data.size.toLong()
                    is Zero -> count
                    is Document -> length
                }
        }

        private class Blob(val parts: List<Part>) {
            val size: Long = parts.sumOf { it.size }

            fun padded(alignment: Int): Blob {
                val target = ((size + alignment - 1L) / alignment) * alignment
                return if (target == size) this else Blob(parts + Part.Zero(target - size))
            }

            fun digest(cancelled: () -> Boolean): ByteArray {
                val md = MessageDigest.getInstance("SHA-256")
                val out = object : OutputStream() {
                    override fun write(b: Int) { md.update(b.toByte()) }
                    override fun write(b: ByteArray, off: Int, len: Int) { md.update(b, off, len) }
                }
                writeTo(out, cancelled)
                return md.digest()
            }

            fun readHead(count: Int, cancelled: () -> Boolean): ByteArray {
                val out = ByteArrayOutputStream(count)
                var remaining = count
                for (part in parts) {
                    if (remaining <= 0) break
                    if (cancelled()) throw CancellationException("Packaging cancelled")
                    when (part) {
                        is Part.Bytes -> {
                            val n = min(remaining, part.data.size)
                            out.write(part.data, 0, n)
                            remaining -= n
                        }
                        is Part.Zero -> {
                            val n = min(remaining.toLong(), part.count).toInt()
                            out.write(ByteArray(n))
                            remaining -= n
                        }
                        is Part.Document -> {
                            part.opener(part.uri)?.use { input ->
                                val buffer = ByteArray(min(1024 * 1024, remaining))
                                while (remaining > 0) {
                                    if (cancelled()) throw CancellationException("Packaging cancelled")
                                    val n = input.read(buffer, 0, min(buffer.size, remaining))
                                    if (n < 0) break
                                    out.write(buffer, 0, n)
                                    remaining -= n
                                }
                            } ?: error("Unable to read source file")
                        }
                    }
                }
                return out.toByteArray()
            }

            fun writeTo(
                output: OutputStream,
                cancelled: () -> Boolean,
                onBytes: ((Long) -> Unit)? = null
            ) {
                val buffer = ByteArray(4 * 1024 * 1024)
                val zeros = ByteArray(1024 * 1024)
                for (part in parts) {
                    if (cancelled()) throw CancellationException("Packaging cancelled")
                    when (part) {
                        is Part.Bytes -> {
                            output.write(part.data)
                            onBytes?.invoke(part.data.size.toLong())
                        }
                        is Part.Zero -> {
                            var left = part.count
                            while (left > 0) {
                                if (cancelled()) throw CancellationException("Packaging cancelled")
                                val n = min(left, zeros.size.toLong()).toInt()
                                output.write(zeros, 0, n)
                                left -= n
                                onBytes?.invoke(n.toLong())
                            }
                        }
                        is Part.Document -> {
                            part.opener(part.uri)?.use { input ->
                                var remaining = part.length
                                while (remaining > 0) {
                                    if (cancelled()) throw CancellationException("Packaging cancelled")
                                    val expected = min(remaining, buffer.size.toLong()).toInt()
                                    val n = input.read(buffer, 0, expected)
                                    if (n <= 0) error("Source file changed or ended early")
                                    output.write(buffer, 0, n)
                                    remaining -= n
                                    onBytes?.invoke(n.toLong())
                                }
                            } ?: error("Unable to read source file")
                        }
                    }
                }
            }

            fun forEachBlock(
                blockSize: Int,
                cancelled: () -> Boolean,
                padLast: Boolean,
                consumer: (ByteArray, Int) -> Unit
            ) {
                val block = ByteArray(blockSize)
                var filled = 0
                fun push(bytes: ByteArray, offset: Int, length: Int) {
                    var at = offset
                    var remaining = length
                    while (remaining > 0) {
                        val n = min(blockSize - filled, remaining)
                        bytes.copyInto(block, filled, at, at + n)
                        filled += n
                        at += n
                        remaining -= n
                        if (filled == blockSize) {
                            consumer(block, blockSize)
                            block.fill(0)
                            filled = 0
                        }
                    }
                }
                val ioBuffer = ByteArray(4 * 1024 * 1024)
                val zeros = ByteArray(1024 * 1024)
                for (part in parts) {
                    if (cancelled()) throw CancellationException("Packaging cancelled")
                    when (part) {
                        is Part.Bytes -> push(part.data, 0, part.data.size)
                        is Part.Zero -> {
                            var left = part.count
                            while (left > 0) {
                                val n = min(left, zeros.size.toLong()).toInt()
                                push(zeros, 0, n)
                                left -= n
                            }
                        }
                        is Part.Document -> {
                            part.opener(part.uri)?.use { input ->
                                var remaining = part.length
                                while (remaining > 0) {
                                    if (cancelled()) throw CancellationException("Packaging cancelled")
                                    val expected = min(remaining, ioBuffer.size.toLong()).toInt()
                                    val n = input.read(ioBuffer, 0, expected)
                                    if (n <= 0) error("Source file changed or ended early")
                                    push(ioBuffer, 0, n)
                                    remaining -= n
                                }
                            } ?: error("Unable to read source file")
                        }
                    }
                }
                if (filled > 0) {
                    if (padLast) {
                        block.fill(0, filled, blockSize)
                    }
                    consumer(block, filled)
                }
            }

            companion object {
                fun bytes(data: ByteArray) = Blob(listOf(Part.Bytes(data)))
                fun document(uri: Uri, length: Long, opener: (Uri) -> InputStream?) =
                    Blob(listOf(Part.Document(uri, length, opener)))
            }
        }
    }
}
