/* Attenova v9.1 — live student-list search + select-and-send.
   Works purely from data attributes, so any student table can opt in:
   - <input data-iba-search="TABLE_ID"> filters that table's rows live
     as the teacher types (no page reload). A row matches its visible
     text plus its data-iba-search value (roll no, name, CMS ID, email,
     portal password). Rows that do not match are hidden; when nothing
     matches, the row marked data-iba-search-empty="TABLE_ID" is shown.
   - <button data-iba-search-clear="TABLE_ID"> clears that search box.
   - <button data-iba-select-toggle="TABLE_ID"> shows/hides the row
     checkboxes and the Send Email button for that table ("Select
     Students" becomes "Done").
   - <input type="checkbox" data-iba-select-all="TABLE_ID"> in the
     header checks all currently visible rows; the button marked
     data-iba-select-send="TABLE_ID" stays disabled until at least one
     row is checked, and shows how many are selected. */
(function () {
    'use strict';

    function tableById(id) { return document.getElementById(id); }

    function rowText(row) {
        var extra = row.getAttribute('data-iba-search') || '';
        return (row.textContent + ' ' + extra).toLowerCase();
    }

    function dataRows(table) {
        var rows = [];
        table.querySelectorAll('tbody tr').forEach(function (row) {
            if (!row.hasAttribute('data-iba-search-empty')) rows.push(row);
        });
        return rows;
    }

    function emptyRow(table) {
        return table.querySelector('[data-iba-search-empty]');
    }

    function rowCheckboxes(table) {
        return table.querySelectorAll('input.iba-row-select');
    }

    function visibleRowCheckboxes(table) {
        var out = [];
        rowCheckboxes(table).forEach(function (cb) {
            var row = cb.closest('tr');
            if (row && row.style.display !== 'none') out.push(cb);
        });
        return out;
    }

    function syncSelectSend(table) {
        var sendBtn = document.querySelector(
            '[data-iba-select-send="' + table.id + '"]');
        if (!sendBtn) return;
        var checked = 0;
        rowCheckboxes(table).forEach(function (cb) {
            if (cb.checked) checked += 1;
        });
        sendBtn.disabled = checked === 0;
        sendBtn.textContent = checked > 0
            ? 'Send Email (' + checked + ')' : 'Send Email';
        var allBox = table.querySelector('[data-iba-select-all]');
        if (allBox) {
            var visible = visibleRowCheckboxes(table);
            allBox.checked = visible.length > 0 && visible.every(
                function (cb) { return cb.checked; });
        }
    }

    function applySearch(table, query) {
        var q = (query || '').trim().toLowerCase();
        var visible = 0;
        dataRows(table).forEach(function (row) {
            var show = !q || rowText(row).indexOf(q) !== -1;
            row.style.display = show ? '' : 'none';
            if (show) visible += 1;
        });
        var empty = emptyRow(table);
        if (empty) empty.style.display = (q && visible === 0) ? '' : 'none';
        syncSelectSend(table);
    }

    function wireSearch(input) {
        var table = tableById(input.getAttribute('data-iba-search'));
        if (!table) return;
        var clearBtn = document.querySelector(
            '[data-iba-search-clear="' +
            input.getAttribute('data-iba-search') + '"]');
        function onInput() {
            applySearch(table, input.value);
            if (clearBtn) clearBtn.classList.toggle('hidden', !input.value);
        }
        input.addEventListener('input', onInput);
        if (clearBtn) {
            clearBtn.addEventListener('click', function () {
                input.value = '';
                onInput();
                input.focus();
            });
        }
    }

    function wireSelectToggle(btn) {
        var table = tableById(btn.getAttribute('data-iba-select-toggle'));
        if (!table) return;
        btn.addEventListener('click', function () {
            var selecting = btn.getAttribute('data-iba-selecting') !== '1';
            btn.setAttribute('data-iba-selecting', selecting ? '1' : '0');
            btn.textContent = selecting ? 'Done' : 'Select Students';
            table.querySelectorAll('.iba-select-col').forEach(function (cell) {
                cell.classList.toggle('hidden', !selecting);
            });
            var sendBtn = document.querySelector(
                '[data-iba-select-send="' + table.id + '"]');
            if (sendBtn) sendBtn.classList.toggle('hidden', !selecting);
            if (!selecting) {
                rowCheckboxes(table).forEach(function (cb) {
                    cb.checked = false;
                });
            }
            syncSelectSend(table);
        });
    }

    function wireSelectAll(box) {
        var table = tableById(box.getAttribute('data-iba-select-all'));
        if (!table) return;
        box.addEventListener('change', function () {
            visibleRowCheckboxes(table).forEach(function (cb) {
                cb.checked = box.checked;
            });
            syncSelectSend(table);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-iba-search]').forEach(function (el) {
            if (el.tagName === 'INPUT') wireSearch(el);
        });
        document.querySelectorAll('[data-iba-select-toggle]')
            .forEach(wireSelectToggle);
        document.querySelectorAll('[data-iba-select-all]')
            .forEach(wireSelectAll);
        document.querySelectorAll('input.iba-row-select').forEach(
            function (cb) {
                cb.addEventListener('change', function () {
                    syncSelectSend(cb.closest('table'));
                });
            });
    });
})();
