package org.nx2a.stage1;

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
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.IOException;
import java.util.Locale;

public final class MainActivity extends Activity {
    private static final int REQUEST_EXEFS = 1001;

    static {
        System.loadLibrary("stage1");
    }

    private native String inspectExeFs(
            int mainFd, long mainSize,
            int npdmFd, long npdmSize);

    private TextView reportView;
    private Button chooseButton;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(36, 36, 36, 36);

        TextView title = new TextView(this);
        title.setText("NX2 Switch 2 - Stage 1A");
        title.setTextSize(24f);
        title.setGravity(Gravity.CENTER_HORIZONTAL);
        root.addView(title, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        TextView help = new TextView(this);
        help.setText(
                "Choose the extracted ExeFS folder that contains main and main.npdm.\n\n" +
                "This build only reads their headers. It does not modify, decrypt, upload, " +
                "or execute the game yet. The report is copied to your clipboard automatically.");
        help.setTextSize(16f);
        LinearLayout.LayoutParams helpParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        helpParams.setMargins(0, 24, 0, 24);
        root.addView(help, helpParams);

        chooseButton = new Button(this);
        chooseButton.setText("Choose ExeFS folder");
        chooseButton.setOnClickListener(v -> chooseExeFs());
        root.addView(chooseButton, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        reportView = new TextView(this);
        reportView.setText("No folder selected yet.");
        reportView.setTextSize(13f);
        reportView.setTextIsSelectable(true);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(reportView);
        LinearLayout.LayoutParams scrollParams = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f);
        scrollParams.setMargins(0, 24, 0, 0);
        root.addView(scroll, scrollParams);

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

        chooseButton.setEnabled(false);
        reportView.setText("Inspecting selected ExeFS folder...");

        try {
            Uri[] files = findRequiredFiles(tree);
            if (files[0] == null || files[1] == null) {
                StringBuilder missing = new StringBuilder(
                        "I could not find both required files in that folder.\n\n");
                missing.append("main: ")
                        .append(files[0] == null ? "MISSING" : "found").append('\n');
                missing.append("main.npdm: ")
                        .append(files[1] == null ? "MISSING" : "found").append('\n');
                missing.append("\nChoose the ExeFS folder itself, not the RomFS folder.");
                setReport(missing.toString(), false);
                return;
            }

            ContentResolver resolver = getContentResolver();
            try (ParcelFileDescriptor mainPfd = resolver.openFileDescriptor(files[0], "r");
                 ParcelFileDescriptor npdmPfd = resolver.openFileDescriptor(files[1], "r")) {
                if (mainPfd == null || npdmPfd == null) {
                    throw new IOException("Android could not open one of the selected files");
                }
                String report = inspectExeFs(
                        mainPfd.getFd(), mainPfd.getStatSize(),
                        npdmPfd.getFd(), npdmPfd.getStatSize());
                setReport(report, true);
            }
        } catch (Exception e) {
            setReport("Stage 1A failed before inspection:\n" + e, false);
        } finally {
            chooseButton.setEnabled(true);
        }
    }

    private Uri[] findRequiredFiles(Uri tree) {
        Uri main = null;
        Uri npdm = null;
        String treeId = DocumentsContract.getTreeDocumentId(tree);
        Uri children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, treeId);
        String[] projection = {
                DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME
        };

        try (Cursor cursor = getContentResolver().query(
                children, projection, null, null, null)) {
            if (cursor == null) {
                return new Uri[]{null, null};
            }
            int idIndex = cursor.getColumnIndexOrThrow(
                    DocumentsContract.Document.COLUMN_DOCUMENT_ID);
            int nameIndex = cursor.getColumnIndexOrThrow(
                    DocumentsContract.Document.COLUMN_DISPLAY_NAME);
            while (cursor.moveToNext()) {
                String id = cursor.getString(idIndex);
                String name = cursor.getString(nameIndex);
                if (name == null) {
                    continue;
                }
                String lower = name.toLowerCase(Locale.ROOT);
                Uri doc = DocumentsContract.buildDocumentUriUsingTree(tree, id);
                if (lower.equals("main")) {
                    main = doc;
                } else if (lower.equals("main.npdm")) {
                    npdm = doc;
                }
            }
        }
        return new Uri[]{main, npdm};
    }

    private void setReport(String report, boolean copied) {
        reportView.setText(report);
        if (copied) {
            ClipboardManager clipboard =
                    (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            clipboard.setPrimaryClip(
                    ClipData.newPlainText("NX2 Switch 2 Stage 1A", report));
            new AlertDialog.Builder(this)
                    .setTitle("Stage 1A complete")
                    .setMessage("The ExeFS report was copied to your clipboard. Paste it into ChatGPT.")
                    .setPositiveButton("OK", null)
                    .show();
        }
    }
}
