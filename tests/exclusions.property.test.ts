import * as hegel from '@hegeldev/hegel';
import * as gs from '@hegeldev/hegel/generators';
import { expect, test } from 'vitest';

import { isSibling } from '../src/retrieve/exclusions.js';
import type { RetrievalQuery } from '../src/retrieve/retrieval.js';
import type { SiblingColumns } from '../src/store/reader.js';

const queryForSession = (sessionUuid: string): RetrievalQuery => ({
  work_id: 'query-work',
  rig: 'rig',
  started: '2026-01-01T00:00:00Z',
  errors: [],
  session_uuid: sessionUuid,
});

const recordForSessions = (sessionUuids: readonly string[]): SiblingColumns => ({
  work_id: 'candidate-work',
  convoy_id: null,
  pr: null,
  external_ref: null,
  parent: null,
  session_uuids: sessionUuids,
});

test('session sibling status follows exact transcript membership', () =>
  hegel.test(tc => {
    const sessionUuid = tc.draw(gs.uuids());
    const generatedSessions = tc.draw(gs.arrays(gs.uuids()));
    const otherSessions = generatedSessions.filter(candidate => candidate !== sessionUuid);
    const query = queryForSession(sessionUuid);

    expect(isSibling(recordForSessions(otherSessions), query)).toBe(false);
    expect(isSibling(recordForSessions([...otherSessions, sessionUuid]), query)).toBe(true);
  }));
