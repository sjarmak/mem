import { z } from 'zod';

/**
 * Lesson payload taxonomy — the engram progressive-disclosure observation
 * model (sjarmak/engram CLAUDE_MEM_INTEGRATION.md, E14) adapted to mem's
 * append-only lessons (Decision 9). The payload stays freeform JSON; this
 * schema is the documented *convention* for its well-known fields, so
 * distillers that emit them and the disclosure layer that projects them
 * agree on shape. Unknown keys pass through untouched.
 */

/** The dual-dimension classification: WHY a lesson matters (its type/WHAT is
 * the work record itself). Fixed engram taxonomy, deliberately closed — a
 * richer lesson taxonomy than flat text, not an open label set. */
export const ConceptTagSchema = z.enum([
  'how-it-works',
  'why-it-exists',
  'what-changed',
  'problem-solution',
  'gotcha',
  'pattern',
  'trade-off',
]);

export type ConceptTag = z.infer<typeof ConceptTagSchema>;

/**
 * The progressive-disclosure lesson payload convention (engram L2 "details"):
 * `subtitle` (one sentence), `facts` (self-contained statements), `narrative`
 * (full context), `concepts` (the taxonomy above). All optional — historical
 * lessons predate the convention — but when present they must be well-formed,
 * so a typo'd concept tag fails at insert, not silently at retrieval.
 */
export const LessonPayloadSchema = z
  .object({
    subtitle: z.string().optional(),
    facts: z.array(z.string()).optional(),
    narrative: z.string().optional(),
    concepts: z.array(ConceptTagSchema).optional(),
  })
  .passthrough();

export type LessonPayload = z.infer<typeof LessonPayloadSchema>;
