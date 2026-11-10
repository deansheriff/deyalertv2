import 'dart:convert';
import 'dart:math';

import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';

enum QueueState { pending, sending, sent, failed }

class QueueItem {
  const QueueItem({
    required this.clientId,
    required this.kind,
    required this.path,
    required this.body,
    this.state = QueueState.pending,
    this.attempts = 0,
    this.nextAttemptAt = 0,
    this.mediaPath,
    this.mediaKind,
  });

  final String clientId;
  final String kind;
  final String path;
  final Map<String, dynamic> body;
  final QueueState state;
  final int attempts;
  final int nextAttemptAt;
  final String? mediaPath;
  final String? mediaKind;

  QueueItem copyWith({QueueState? state, int? attempts, int? nextAttemptAt}) =>
      QueueItem(
        clientId: clientId,
        kind: kind,
        path: path,
        body: body,
        state: state ?? this.state,
        attempts: attempts ?? this.attempts,
        nextAttemptAt: nextAttemptAt ?? this.nextAttemptAt,
        mediaPath: mediaPath,
        mediaKind: mediaKind,
      );
}

abstract interface class QueueStore {
  Future<void> upsert(QueueItem item);
  Future<List<QueueItem>> all();
  Future<void> remove(String clientId);
}

enum SendOutcome { sent, retry, rejected }

Duration backoffFor(int attempts) =>
    Duration(seconds: min(3600, 15 * pow(2, attempts).toInt()));

SendOutcome outcomeForStatus(int? status) {
  if (status == null) return SendOutcome.retry;
  if (status >= 200 && status < 300) return SendOutcome.sent;
  if (status == 401 || status == 408 || status == 429 || status >= 500) {
    return SendOutcome.retry;
  }
  return SendOutcome.rejected;
}

class OfflineQueue {
  OfflineQueue(this.store, {DateTime Function()? clock})
    : _clock = clock ?? DateTime.now;

  final QueueStore store;
  final DateTime Function() _clock;
  bool _flushing = false;

  Future<void> enqueue(QueueItem item) => store.upsert(item);

  Future<int> pendingCount() async => (await store.all())
      .where((item) => item.state != QueueState.failed)
      .length;

  Future<void> retryFailed() async {
    for (final item in await store.all()) {
      if (item.state == QueueState.failed) {
        await store.upsert(
          item.copyWith(state: QueueState.pending, nextAttemptAt: 0),
        );
      }
    }
  }

  /// Sends everything due. Calling it while a flush is running does nothing,
  /// so one report is never in flight twice.
  Future<int> flush(Future<SendOutcome> Function(QueueItem) send) async {
    if (_flushing) return 0;
    _flushing = true;
    var sent = 0;
    try {
      final now = _clock().millisecondsSinceEpoch;
      for (final item in await store.all()) {
        if (item.state == QueueState.failed || item.nextAttemptAt > now) {
          continue;
        }
        await store.upsert(item.copyWith(state: QueueState.sending));
        final outcome = await send(item);
        switch (outcome) {
          case SendOutcome.sent:
            await store.remove(item.clientId);
            sent++;
          case SendOutcome.rejected:
            await store.upsert(item.copyWith(state: QueueState.failed));
          case SendOutcome.retry:
            final attempts = item.attempts + 1;
            await store.upsert(
              item.copyWith(
                state: QueueState.pending,
                attempts: attempts,
                nextAttemptAt: _clock()
                    .add(backoffFor(attempts))
                    .millisecondsSinceEpoch,
              ),
            );
        }
      }
    } finally {
      _flushing = false;
    }
    return sent;
  }
}

class SqliteQueueStore implements QueueStore {
  SqliteQueueStore(this.db);

  final Database db;

  static Future<Database> open() async => openDatabase(
    p.join(await getDatabasesPath(), 'dey_alert.db'),
    version: 1,
    onCreate: (db, _) async {
      await db.execute('''
        CREATE TABLE queue (
          client_id TEXT PRIMARY KEY, kind TEXT, path TEXT, body TEXT,
          state TEXT, attempts INTEGER, next_at INTEGER,
          media_path TEXT, media_kind TEXT, created_at INTEGER
        )''');
      await db.execute(
        'CREATE TABLE cache (key TEXT PRIMARY KEY, value TEXT, saved_at INTEGER)',
      );
    },
  );

  @override
  Future<void> upsert(QueueItem item) async {
    await db.insert('queue', {
      'client_id': item.clientId,
      'kind': item.kind,
      'path': item.path,
      'body': jsonEncode(item.body),
      'state': item.state.name,
      'attempts': item.attempts,
      'next_at': item.nextAttemptAt,
      'media_path': item.mediaPath,
      'media_kind': item.mediaKind,
      'created_at': DateTime.now().millisecondsSinceEpoch,
    }, conflictAlgorithm: ConflictAlgorithm.replace);
  }

  @override
  Future<List<QueueItem>> all() async {
    final rows = await db.query('queue', orderBy: 'created_at');
    return rows
        .map(
          (row) => QueueItem(
            clientId: row['client_id']! as String,
            kind: row['kind']! as String,
            path: row['path']! as String,
            body: jsonDecode(row['body']! as String) as Map<String, dynamic>,
            state: QueueState.values.byName(row['state']! as String),
            attempts: row['attempts']! as int,
            nextAttemptAt: row['next_at']! as int,
            mediaPath: row['media_path'] as String?,
            mediaKind: row['media_kind'] as String?,
          ),
        )
        .toList();
  }

  @override
  Future<void> remove(String clientId) =>
      db.delete('queue', where: 'client_id = ?', whereArgs: [clientId]);
}
