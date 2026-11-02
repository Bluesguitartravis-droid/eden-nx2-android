package org.nx2a.stage1c;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentResolver;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.ParcelFileDescriptor;
import android.provider.DocumentsContract;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.IOException;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

public final class MainActivity extends Activity {
    private static final int REQUEST_EXEFS = 1301;

    static {
        System.loadLibrary("stage1c");
    }

    private native String runBootstrap(
            int mainFd, long mainSize,
            int sdkFd, long sdkSize,
            int rtldFd, long rtldSize,
            int npdmFd, long npdmSize);

    private TextView reportView;
    private Button runButton;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(36, 36, 36, 36);

        TextView title = new TextView(this);
        title.setText("NX2 Switch 2 - Stage 1C");
        title.setTextSize(24f);
        title.setGravity(Gravity.CENTER_HORIZONTAL);
        root.addView(title);

        TextView help = new TextView(this);
        help.setText(
                "FIRST CONTROLLED GUEST EXECUTION\\n\\n" +
                "Choose the same extracted ExeFS folder. This build is hard-locked to the " +
                "title/build IDs from the Stage 1B.2 pass. It maps and relocates main + sdk, " +
                "rewrites Android-incompatible ARM64 instructions into guarded traps, then " +
                "starts the SDK bootstrap.\\n\\n" +
                "rtld is validated but not executed because the native host replaces its loader role. " +
                "Graphics and RomFS are still disabled.");
        help.setTextSize(16f);
        LinearLayout.LayoutParams hp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        hp.setMargins(0, 24, 0, 24);
        root.addView(help, hp);

        runButton = new Button(this);
        runButton.setText("Choose ExeFS and run Stage 1C");
        runButton.setOnClickListener(v -> chooseExeFs());
        root.addView(runButton);

        reportView = new TextView(this);
        reportView.setText("No execution attempted yet.");
        reportView.setTextSize(13f);
        reportView.setTextIsSelectable(true);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(reportView);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f);
        sp.setMargins(0, 24, 0, 0);
        root.addView(scroll, sp);

        setContentView(root);
    }

    private void chooseExeFs() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION
                | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
                | Intent.FLAG_GRANT_PREFIX_URI_PERMISSION);
        startActivityForResult(intent, REQUEST_EXEFS);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQUEST_EXEFS || resultCode != RESULT_OK
                || data == null || data.getData() == null) {
            return;
        }

        Uri tree = data.getData();
        try {
            getContentResolver().takePersistableUriPermission(
                    tree, Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (SecurityException ignored) {
        }

        Map<String, Uri> files = enumerate(tree);
        String[] required = {"main", "sdk", "rtld", "main.npdm"};
        StringBuilder missing = new StringBuilder();
        for (String name : required) {
            if (!files.containsKey(name)) {
                if (missing.length() != 0) missing.append(", ");
                missing.append(name);
            }
        }
        if (missing.length() != 0) {
            setReport(
                    "Stage 1C refused to run. Missing from selected ExeFS: " +
                    missing + "\n\nChoose the same complete ExeFS folder used for Stage 1B.2.",
                    false);
            return;
        }

        runButton.setEnabled(false);
        reportView.setText(
                "Preparing controlled guest execution...\\n" +
                "If guest code reaches an unsupported dependency, the harness will trap it and report it.");

        new Thread(() -> {
            ContentResolver resolver = getContentResolver();
            try (ParcelFileDescriptor mainPfd =
                         resolver.openFileDescriptor(files.get("main"), "r");
                 ParcelFileDescriptor sdkPfd =
                         resolver.openFileDescriptor(files.get("sdk"), "r");
                 ParcelFileDescriptor rtldPfd =
                         resolver.openFileDescriptor(files.get("rtld"), "r");
                 ParcelFileDescriptor npdmPfd =
                         resolver.openFileDescriptor(files.get("main.npdm"), "r")) {

                if (mainPfd == null || sdkPfd == null
                        || rtldPfd == null || npdmPfd == null) {
                    throw new IOException("Android could not open one of the ExeFS files");
                }

                String report = runBootstrap(
                        mainPfd.getFd(), mainPfd.getStatSize(),
                        sdkPfd.getFd(), sdkPfd.getStatSize(),
                        rtldPfd.getFd(), rtldPfd.getStatSize(),
                        npdmPfd.getFd(), npdmPfd.getStatSize());

                runOnUiThread(() -> setReport(report, true));
            } catch (Exception e) {
                runOnUiThread(() ->
                        setReport("Stage 1C failed before native bootstrap:\n" + e, false));
            } finally {
                runOnUiThread(() -> runButton.setEnabled(true));
            }
        }, "nx2-stage1c").start();
    }

    private Map<String, Uri> enumerate(Uri tree) {
        Map<String, Uri> out = new HashMap<>();
        String treeId = DocumentsContract.getTreeDocumentId(tree);
        Uri children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, treeId);
        String[] projection = {
                DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME
        };

        try (Cursor cursor = getContentResolver().query(
                children, projection, null, null, null)) {
            if (cursor == null) return out;

            int idIndex = cursor.getColumnIndexOrThrow(
                    DocumentsContract.Document.COLUMN_DOCUMENT_ID);
            int nameIndex = cursor.getColumnIndexOrThrow(
                    DocumentsContract.Document.COLUMN_DISPLAY_NAME);

            while (cursor.moveToNext()) {
                String id = cursor.getString(idIndex);
                String name = cursor.getString(nameIndex);
                if (name == null) continue;
                String lower = name.toLowerCase(Locale.ROOT);
                if (lower.equals("main") || lower.equals("sdk")
                        || lower.equals("rtld") || lower.equals("main.npdm")) {
                    out.put(lower,
                            DocumentsContract.buildDocumentUriUsingTree(tree, id));
                }
            }
        }
        return out;
    }

    private void setReport(String report, boolean copied) {
        reportView.setText(report);
        if (copied) {
            ClipboardManager clipboard =
                    (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            clipboard.setPrimaryClip(
                    ClipData.newPlainText("NX2 Switch 2 Stage 1C", report));

            new AlertDialog.Builder(this)
                    .setTitle("Stage 1C finished")
                    .setMessage(
                            "The controlled execution report was copied to your clipboard. " +
                            "Paste the complete report into ChatGPT.")
                    .setPositiveButton("OK", null)
                    .show();
        }
    }
}
