package org.nx2a.stage1b2;

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
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;

public final class MainActivity extends Activity {
    private static final int REQUEST_EXEFS = 1201;

    static {
        System.loadLibrary("stage1b2");
    }

    private native String analyzeModules(
            int[] fds, long[] sizes, String[] names,
            int npdmFd, long npdmSize);

    private TextView reportView;
    private Button chooseButton;

    private static final class ModuleDoc {
        final String name;
        final Uri uri;
        ModuleDoc(String name, Uri uri) {
            this.name = name;
            this.uri = uri;
        }
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(36, 36, 36, 36);

        TextView title = new TextView(this);
        title.setText("NX2 Switch 2 - Stage 1B.2");
        title.setTextSize(24f);
        title.setGravity(Gravity.CENTER_HORIZONTAL);
        root.addView(title);

        TextView help = new TextView(this);
        help.setText(
                "Choose the extracted ExeFS folder. This build scans every executable module " +
                "(rtld, main, subsdk*, sdk) so we know the complete rewrite workload before " +
                "the first native execution attempt. Nothing is executed or modified.");
        help.setTextSize(16f);
        LinearLayout.LayoutParams hp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        hp.setMargins(0, 24, 0, 24);
        root.addView(help, hp);

        chooseButton = new Button(this);
        chooseButton.setText("Choose ExeFS folder");
        chooseButton.setOnClickListener(v -> chooseExeFs());
        root.addView(chooseButton);

        reportView = new TextView(this);
        reportView.setText("No folder selected yet.");
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

        final List<ModuleDoc> modules = new ArrayList<>();
        final Uri npdm = enumerateExeFs(tree, modules);
        if (npdm == null || modules.isEmpty()) {
            setReport(
                    "Could not find main.npdm plus executable modules in that folder.\n" +
                    "Choose the ExeFS folder itself.", false);
            return;
        }

        modules.sort(Comparator.comparingInt(m -> moduleOrder(m.name)));

        chooseButton.setEnabled(false);
        reportView.setText("Scanning all ExeFS modules...");

        new Thread(() -> {
            List<ParcelFileDescriptor> opened = new ArrayList<>();
            try {
                ContentResolver resolver = getContentResolver();
                int[] fds = new int[modules.size()];
                long[] sizes = new long[modules.size()];
                String[] names = new String[modules.size()];

                for (int i = 0; i < modules.size(); ++i) {
                    ParcelFileDescriptor pfd =
                            resolver.openFileDescriptor(modules.get(i).uri, "r");
                    if (pfd == null) {
                        throw new IOException("Could not open " + modules.get(i).name);
                    }
                    opened.add(pfd);
                    fds[i] = pfd.getFd();
                    sizes[i] = pfd.getStatSize();
                    names[i] = modules.get(i).name;
                }

                try (ParcelFileDescriptor npdmPfd =
                             resolver.openFileDescriptor(npdm, "r")) {
                    if (npdmPfd == null) {
                        throw new IOException("Could not open main.npdm");
                    }
                    String report = analyzeModules(
                            fds, sizes, names,
                            npdmPfd.getFd(), npdmPfd.getStatSize());
                    runOnUiThread(() -> setReport(report, true));
                }
            } catch (Exception e) {
                runOnUiThread(() ->
                        setReport("Stage 1B.2 failed:\n" + e, false));
            } finally {
                for (ParcelFileDescriptor pfd : opened) {
                    try { pfd.close(); } catch (IOException ignored) {}
                }
                runOnUiThread(() -> chooseButton.setEnabled(true));
            }
        }, "nx2-stage1b2").start();
    }

    private Uri enumerateExeFs(Uri tree, List<ModuleDoc> modules) {
        Uri npdm = null;
        String treeId = DocumentsContract.getTreeDocumentId(tree);
        Uri children = DocumentsContract.buildChildDocumentsUriUsingTree(tree, treeId);
        String[] projection = {
                DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME
        };

        try (Cursor cursor = getContentResolver().query(
                children, projection, null, null, null)) {
            if (cursor == null) return null;
            int idIndex = cursor.getColumnIndexOrThrow(
                    DocumentsContract.Document.COLUMN_DOCUMENT_ID);
            int nameIndex = cursor.getColumnIndexOrThrow(
                    DocumentsContract.Document.COLUMN_DISPLAY_NAME);

            while (cursor.moveToNext()) {
                String id = cursor.getString(idIndex);
                String name = cursor.getString(nameIndex);
                if (name == null) continue;
                String lower = name.toLowerCase(Locale.ROOT);
                Uri doc = DocumentsContract.buildDocumentUriUsingTree(tree, id);

                if (lower.equals("main.npdm")) {
                    npdm = doc;
                } else if (isExecutableModule(lower)) {
                    modules.add(new ModuleDoc(lower, doc));
                }
            }
        }
        return npdm;
    }

    private static boolean isExecutableModule(String name) {
        return name.equals("rtld")
                || name.equals("main")
                || name.equals("sdk")
                || name.matches("subsdk\\d+");
    }

    private static int moduleOrder(String name) {
        if (name.equals("rtld")) return 0;
        if (name.equals("main")) return 1;
        if (name.startsWith("subsdk")) {
            try {
                return 10 + Integer.parseInt(name.substring(6));
            } catch (NumberFormatException ignored) {
                return 99;
            }
        }
        if (name.equals("sdk")) return 1000;
        return 2000;
    }

    private void setReport(String report, boolean copied) {
        reportView.setText(report);
        if (copied) {
            ClipboardManager clipboard =
                    (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            clipboard.setPrimaryClip(
                    ClipData.newPlainText("NX2 Switch 2 Stage 1B.2", report));
            new AlertDialog.Builder(this)
                    .setTitle("Full ExeFS census complete")
                    .setMessage("The complete module report was copied. Paste it into ChatGPT.")
                    .setPositiveButton("OK", null)
                    .show();
        }
    }
}
