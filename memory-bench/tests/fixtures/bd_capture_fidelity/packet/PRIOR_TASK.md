Source event line 26; timestamp 2026-07-17T16:01:27.574Z

○ EnterpriseBench-t2dl4 [BUG] · cost_tracker/_walk_trace catch only JSONDecodeError: a bare ValueError from json.loads aborts the whole batch cost report   [● P1 · OPEN]
Owner: enterprisebench-worker-gc-476013 · Type: bug
Created: 2026-07-15 · Updated: 2026-07-17

DESCRIPTION

  Found by the security reviewer during EnterpriseBench-4p5k's Phase 4 (2026- 
  07-15). PRE-EXISTING                                                        
  (both lines predate 4p5k's diff); filed rather than fixed inline to keep    
  4p5k single-concern.                                                        
                                                                              
  Both trace parse boundaries catch ONLY json.JSONDecodeError:                
  scripts/cost_tracker.py:201            except json.JSONDecodeError: warn +  
  continue                                                                    
  lib/eb_metrics/retrieval_extraction.py:380   except json.JSONDecodeError:   
  continue                                                                    
                                                                              
  json.loads can raise a BARE ValueError that this does not catch.            
  JSONDecodeError is a SUBCLASS                                               
  of ValueError, so catching the subclass does not catch the parent:          
                                                                              
  |                                                                           
  | |                                                                         
  | | | json.loads('{"input_tokens": ' + '9'*5000 + '}')                      
  | | | ValueError: Exceeds the limit (4300 digits) for integer string        
  | | conversion                                                              
                                                                              
  That is CPython's int-to-str guard (sys.int_info.default_max_str_digits =   
  4300, the                                                                   
  CVE-2020-10735 mitigation). Deep nesting ([[[[...]]]]) likewise raises      
  RecursionError, also                                                        
  uncaught. Verified by execution against both call sites: parse_trace() and  
  compute_ttfr()                                                              
  each crash rather than skipping the line.                                   
                                                                              
  IMPACT: every OTHER malformed line is skipped, but these two abort the      
  caller. cost_tracker.py:517                                                 
  loops parse_trace() over every agent_trace.jsonl under the results dirs with
  no per-trace guard,                                                         
  so ONE corrupt line in ONE run's trace kills cost reporting for the entire  
  batch. Inconsistent                                                         
  with the documented contract at this boundary ("a trace is third-party      
  input"), which is to skip                                                   
  what it cannot read.                                                        
                                                                              
  FIX: catch (json.JSONDecodeError, ValueError, RecursionError) at both sites 
  and treat the line as                                                       
  malformed, exactly as invalid JSON is treated now. Note JSONDecodeError is  
  redundant once ValueError                                                   
  is caught -- keep it named only if it aids the reader. Add a regression test
  per site feeding a                                                          
                                                                              
  | 4300-digit literal and a deeply-nested line; both must fail before the fix.
                                                                              
  NOT A SECURITY ISSUE in practice (an internal harness reading its own       
  traces; no attacker                                                         
  boundary) -- it is a robustness/availability bug. P1, not P0, for that      
  reason.                                                                     



NOTES

  gate: Fix direction is unforked, but it edits                               
  lib/eb_metrics/retrieval_extraction.py — 0nru's module, under Stephanie's   
  live land-vs-sequence call on the quarantined retrieval axis — and          
  scripts/cost_tracker.py, whose output feeds the paper's arm-level cost      
  figures; routed to human on that entanglement rather than on the fix's own  
  complexity.                                                                 



LABELS: needs-human

METADATA
  gc.work_dir: /workspace/projects/EnterpriseBench-t2dl4
  work_dir: /workspace/projects/EnterpriseBench-t2dl4

BLOCKS
  ← ○ EnterpriseBench-bakp0: input convoy for EnterpriseBench-t2dl4 ● P1
