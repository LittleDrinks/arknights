"""Pure CEM and accounting contracts. No simulator, seed features or ML weights."""
import copy
import math
import random

TRAIN_SEEDS = tuple(range(20017, 20025))
DEV_SEEDS = tuple(range(1, 17))
CURVES = (
    (1, 1, 1, 2, 2, 3, 3, 4, 4, 4, 5, 5, 6, 6, 6, 6),
    (1, 1, 2, 3, 3, 4, 4, 5, 5, 6, 6, 6, 6, 6, 6, 6),
    (1, 1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 5, 5, 6, 6),
)


def validate_parameters(parameters, schema):
    if set(parameters) != set(schema):
        raise ValueError('Exactly the eight policy parameters are required')
    for key, spec in schema.items():
        value = parameters[key]
        if spec['kind'] == 'levels':
            if (not isinstance(value, list) or len(value) != spec['length'] or
                    any(type(x) is not int or not spec['min'] <= x <= spec['max'] for x in value) or
                    sorted(value) != value or tuple(value) not in CURVES):
                raise ValueError('Invalid LEVEL_TARGET preset')
        elif (type(value) not in (int, float) or not math.isfinite(value) or
              not spec['min'] <= value <= spec['max'] or
              (spec['kind'] == 'int' and type(value) is not int)):
            raise ValueError(f'Invalid {key}')


class CEM:
    def __init__(self, schema, seed=314159, alpha=0.7, sigma_floor=0.05, category_floor=0.05):
        if not 0 < alpha <= 1 or not 0 < sigma_floor <= .25 or not 0 < category_floor < 1/3:
            raise ValueError('Invalid smoothing/floor')
        self.schema = schema
        self.rng = random.Random(seed)  # search RNG is independent of game seeds
        self.alpha, self.floor, self.category_floor = alpha, sigma_floor, category_floor
        self.mean = {k: (s['default']-s['min'])/(s['max']-s['min'])
                     for k, s in schema.items() if s['kind'] != 'levels'}
        self.variance = dict.fromkeys(self.mean, .25**2)
        self.probs = [1/3]*3

    def state(self):
        return copy.deepcopy({'meanNormalized': self.mean, 'varianceNormalized': self.variance,
                              'sigmaNormalized': {k: math.sqrt(v) for k, v in self.variance.items()},
                              'levelProbabilities': self.probs, 'alpha': self.alpha,
                              'minSigmaNormalized': self.floor, 'minCategoryProbability': self.category_floor})

    def sample(self):
        p = {}
        for key, mean in self.mean.items():
            # Bounded Gaussian by clipping; record this choice, never change hard ranges.
            x = min(1., max(0., self.rng.gauss(mean, math.sqrt(self.variance[key]))))
            s = self.schema[key]
            x = s['min'] + x*(s['max']-s['min'])
            p[key] = math.floor(x+.5) if s['kind'] == 'int' else round(x, 8)
        p['LEVEL_TARGET'] = list(self.rng.choices(CURVES, weights=self.probs, k=1)[0])
        validate_parameters(p, self.schema)
        return p

    def update(self, elites):
        if not elites:
            raise ValueError('No valid elites; refusing CEM update')
        for p in elites:
            validate_parameters(p, self.schema)
        a = self.alpha
        for key in self.mean:
            s = self.schema[key]
            xs = [(p[key]-s['min'])/(s['max']-s['min']) for p in elites]
            mu = sum(xs)/len(xs)
            var = sum((x-mu)**2 for x in xs)/len(xs)
            self.mean[key] = (1-a)*self.mean[key] + a*mu
            self.variance[key] = max(self.floor**2, (1-a)*self.variance[key] + a*var)
        freq = [sum(tuple(p['LEVEL_TARGET']) == curve for p in elites)/len(elites) for curve in CURVES]
        mixed = [(1-a)*old + a*f for old, f in zip(self.probs, freq)]
        # Uniform mixture guarantees an actual lower bound (clamp+renormalize does not).
        self.probs = [self.category_floor + (1-3*self.category_floor)*p for p in mixed]


def validate_record(row, seed):
    if (row.get('config') != 'solo:ABYSS' or type(row.get('seed')) is not int or row['seed'] != seed or
            type(row.get('victory')) is not bool or type(row.get('errors')) is not int or row['errors'] != 0 or
            row.get('errorMessages') or row.get('matchErrors')):
        raise ValueError('Wrong config/seed/outcome or engine errors')
    rounds, boss_round = row.get('roundsPassed'), row.get('bossRound')
    if (type(rounds) is not int or type(boss_round) is not int or boss_round != 14 or not 0 <= rounds <= 15):
        raise ValueError('Invalid rounds')
    diag = row.get('finalDiagnostics') or {}
    reason = diag.get('reason')
    if reason not in ('victory', 'defeat', 'eliminated') or (reason == 'victory') != row['victory']:
        raise ValueError('Missing/invalid natural terminal; loss must remain explicit')
    if type(diag.get('hiddenCleared')) is not bool or type(row.get('hidden')) is not bool:
        raise ValueError('Missing hidden outcome')
    if row['victory'] and rounds != boss_round + int(diag['hiddenCleared']):
        raise ValueError('Victory/rounds mismatch')
    if not row['victory'] and rounds >= boss_round:
        raise ValueError('Defeat/rounds mismatch')
    if not isinstance(diag.get('players'), list) or len(diag['players']) != 1:
        raise ValueError('Missing terminal player')
    if not isinstance(row.get('players'), list) or len(row['players']) != 1:
        raise ValueError('Missing benchmark player')
    experiment = row.get('experiment', {})
    if experiment.get('engineRules') != 'unchanged' or experiment.get('forcedSoloBand') is not None:
        raise ValueError('Unexpected game alteration')
    if 'bossPool' not in diag:
        raise ValueError('Missing boss diagnostics')
    pool = diag['bossPool']
    damage = 0.
    if pool is not None:
        hp, maximum = pool.get('hp'), pool.get('maxHp')
        if (type(hp) not in (int, float) or type(maximum) not in (int, float) or
                not math.isfinite(hp) or not math.isfinite(maximum) or not 0 <= hp <= maximum or maximum <= 0):
            raise ValueError('Invalid actual boss pool')
        damage = (maximum-hp)/maximum
    elif reason != 'eliminated':
        raise ValueError('Boss terminal missing pool')
    return {'victory': row['victory'], 'roundsPassed': rounds, 'reason': reason,
            'bossDeductedFraction': damage, 'auxiliaryFitness': rounds+damage}


def fitness(games, seeds=TRAIN_SEEDS):
    if len(games) != len(seeds) or {g['seed'] for g in games} != set(seeds):
        return None
    if any(g['status'] != 'complete' for g in games):
        return None
    return (sum(g['outcome']['victory'] for g in games),
            sum(g['outcome']['auxiliaryFitness'] for g in games)/len(seeds))


def select(candidates, elite_count):
    valid = [c for c in candidates if c.get('fitness') is not None]
    if len(valid) < elite_count:
        raise ValueError(f'Insufficient valid candidates: {len(valid)} < {elite_count}')
    # Explicit tie-break independent of scheduling order. No layers in fitness.
    return sorted(valid, key=lambda c: (-c['fitness'][0], -c['fitness'][1], c['id']))[:elite_count]


def make_plan(population, generations, smoke=False):
    if smoke:
        return [{'id':f'smoke-{arm}', 'phase':'smoke', 'candidate':arm, 'seed':TRAIN_SEEDS[0], 'status':'unrun'}
                for arm in ('vanilla', 'default')]
    rows = [{'id':f'g{g:02d}-c{c:02d}-s{s}', 'phase':'train', 'generation':g,
             'candidate':f'g{g:02d}-c{c:02d}', 'seed':s, 'status':'unrun'}
            for g in range(generations) for c in range(population) for s in TRAIN_SEEDS]
    rows += [{'id':f'dev-{arm}-s{s}', 'phase':'dev', 'candidate':arm, 'seed':s, 'status':'unrun'}
             for arm in ('frozen', 'default') for s in DEV_SEEDS]
    return rows


def accounting(rows):
    result = {}
    for phase in ('train', 'dev', 'smoke', 'total'):
        part = [r for r in rows if phase == 'total' or r['phase'] == phase]
        counts = {}
        for r in part:
            counts[r['status']] = counts.get(r['status'], 0)+1
        wins = sum(r['status'] == 'complete' and r['outcome']['victory'] for r in part)
        complete = counts.get('complete', 0)
        result[phase] = {'planned':len(part), 'statuses':counts, 'complete':complete, 'victories':wins,
                         'observedVictoriesPerPlanned': wins/len(part) if part else None,
                         'completeWinRate': wins/complete if complete else None,
                         'allPlannedComplete': complete == len(part)}
    return result
