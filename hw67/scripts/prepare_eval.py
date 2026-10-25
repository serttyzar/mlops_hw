"""Build a fixed evaluation set from held-out AmaSum reviews, before inference.

Labels and evidence below were individually inspected by the coding assistant;
a human reviewer must confirm them before calling this human-verified gold.
No model outputs are used to construct or select the cases.
"""
import hashlib
import json
from pathlib import Path

# (claim, expected, evidence/rationale); positions refer to the unchanged HW3 test split.
ANNOTATIONS = [
 ('The reviewer praises the strength of the spark.', 'SUPPORTED', 'one of the best spark'),
 ('The Chicken and Apple treats arrived soft and chewy.', 'CONTRADICTED', 'as hard as cured concrete'),
 ('The castle is exactly 15 centimetres tall.', 'NOT_STATED', 'No dimensions given.'),
 ('After ten hours of operation some fuel remained.', 'SUPPORTED', 'still had 1/4 tank of fuel'),
 ('The reader found the light comfortably dim.', 'CONTRADICTED', 'hurting his eyes'),
 ('The device has a five-year warranty.', 'NOT_STATED', 'No warranty duration given.'),
 ('The reviewer has been adding hemp seeds to shakes.', 'SUPPORTED', 'give hemp seeds a try to add to my shakes'),
 ('The gummy bears are very difficult to remove from the mold.', 'CONTRADICTED', 'pop out pretty much effortlessly'),
 ('The hot sauce was delivered in two days.', 'NOT_STATED', 'No delivery time given.'),
 ('The monocular performed poorly in pitch-black conditions.', 'SUPPORTED', 'I could see very little'),
 ('The reviewer recommends putting more than two cables in each box.', 'CONTRADICTED', "wouldn't recommend more than two cables"),
 ('A complete recharge takes exactly thirty minutes.', 'NOT_STATED', 'No charging duration given.'),
 ('The reviewer likes the citrus smell around the house.', 'SUPPORTED', 'makes my whole house smell good'),
 ('All three watches synchronize without any problems.', 'CONTRADICTED', "same problem with two of them"),
 ('The inflatable toy includes a two-year warranty.', 'NOT_STATED', 'No warranty mentioned.'),
 ('A string can go out of tune within about a minute.', 'SUPPORTED', "literally a minute later it's out"),
 ('The baby was unable to balance at all when sitting on the seat.', 'CONTRADICTED', 'He could balance perfectly with the support'),
 ('The door accessory is made of solid brass.', 'NOT_STATED', 'No material specified.'),
 ('The floor rack can collapse for transport.', 'SUPPORTED', 'collapses so that I can take it everywhere'),
 ('The size chart helped the reviewer choose a perfectly fitting stick.', 'CONTRADICTED', 'stick was way too big'),
 ('The filters arrived three days after ordering.', 'NOT_STATED', 'Delivery timing absent.'),
 ('Both purchased units failed during power surges.', 'SUPPORTED', 'Bought a second one and the same thing happened'),
 ('The power switch maintains a reliable electrical connection.', 'CONTRADICTED', 'power switch has a faulty connection'),
 ('The massage balls come with a lifetime warranty.', 'NOT_STATED', 'Warranty absent.'),
 ('The flashlight can be operated by spinning the wrist.', 'SUPPORTED', 'works with a few spins of the wrist'),
 ('The fan is well balanced and becoming quieter.', 'CONTRADICTED', "isn't balanced and is getting louder every week"),
 ('The game accessory weighs exactly two kilograms.', 'NOT_STATED', 'Weight not specified.'),
 ('Some pieces arrived dirty and the coating was flaking.', 'SUPPORTED', 'black sticky grime; coating flaking off'),
 ('The cleaner left the coloured clothes undamaged.', 'CONTRADICTED', 'effectively bleaches colored fabrics'),
 ('The cart comes with a ten-year warranty.', 'NOT_STATED', 'Warranty not specified.'),
 ('The reviewer likes that the saddlebags are removable.', 'SUPPORTED', 'I love that the saddlebags come off'),
 ('The mask fits adults loosely even at its smallest adjustment.', 'CONTRADICTED', 'fit is rather tight even at max capacity'),
 ('The language course was delivered by express shipping.', 'NOT_STATED', 'Shipping method absent.'),
 ('A first-time sander user found the tool easy to use.', 'SUPPORTED', 'Never used a sander before; Super simple'),
 ('The sun shade is easy to pack back into its case.', 'CONTRADICTED', "very difficult to pack"),
 ('The pillow cover is machine-washable cotton.', 'NOT_STATED', 'No fabric or washing instruction given.'),
 ('The reviewer was surprised by the large size of the craft box.', 'SUPPORTED', 'did not realize how big this box is'),
 ('The saw stand closes smoothly from the fully open position.', 'CONTRADICTED', 'hangs up in the midway position'),
 ('The slide has a maximum load of fifty kilograms.', 'NOT_STATED', 'No maximum load given.'),
 ('The window shade is translucent but still provides shade.', 'SUPPORTED', 'very translucent and it gives a lot of shade'),
 ('The reviewer mastered the lip device immediately without any difficulty.', 'CONTRADICTED', 'took me a few minutes to get the hang of it'),
 ('Every meat sample in the gift pack weighs fifty grams.', 'NOT_STATED', 'Weights absent.'),
 ('The reviewer would have liked Wonder Woman included in the set.', 'SUPPORTED', 'would have made it better ... adding Wonder Woman'),
 ('The printed book contains the advertised ketogenic recipes.', 'CONTRADICTED', 'These are NOT ketogenic recipes'),
 ('The steamer uses exactly 250 watts of power.', 'NOT_STATED', 'Power consumption absent.'),
 ('The reviewer considers the bowls too small for larger dogs.', 'SUPPORTED', 'Do not buy for any dog not considered small/toy sized'),
 ('Installation of the replacement heater took more than an hour.', 'CONTRADICTED', 'took less than 30 minutes'),
 ('Each storage bag holds exactly ninety litres.', 'NOT_STATED', 'Capacity absent.'),
 ('The advertised replacement pump did not fit properly.', 'SUPPORTED', "pump extension isn't long enough to connect"),
 ('The drone remained responsive to the controller throughout its third flight.', 'CONTRADICTED', 'took off with no response to the controller'),
]


def read(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def write(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))


def main():
    train_path, test_path = Path('../hw4/data/train.jsonl'), Path('../hw3/data/test.jsonl')
    train, test = read(train_path), read(test_path)
    training = [dict(id=r['id'], group_id=r['topic'],
                     input=next(m['content'] for m in r['messages'] if m['role'] == 'user')) for r in train]
    golden = []
    for i, (claim, expected, reason) in enumerate(ANNOTATIONS):
        row = test[i]
        user = next(m['content'] for m in row['messages'] if m['role'] == 'user')
        review = user.split('Customer reviews:\n')[1].split('\n2. ')[0]
        slice_name = {'SUPPORTED': 'supported_claims', 'CONTRADICTED': 'contradicted_claims',
                      'NOT_STATED': 'unstated_claims'}[expected]
        golden.append(dict(id=f'gold-{row["id"]}', group_id=row['topic'],
            input=f'{user.splitlines()[0]}\nReview excerpt:\n{review}\n\nClaim: {claim}\n'
                  'Classify the claim using only this excerpt. Choices: SUPPORTED, CONTRADICTED, NOT_STATED.',
            expected=expected, choices=['SUPPORTED', 'CONTRADICTED', 'NOT_STATED'],
            slices=[slice_name], source=f'AmaSum / HW3 test / ASIN {row["id"]} / review 1',
            evidence=reason, review_status='assistant_checked; pending_human_confirmation'))
    behavior = [dict(golden[0], id='behavior-format', type='format'),
        dict(golden[0], id='behavior-paraphrase', type='paraphrase',
             input=golden[0]['input'].replace('The reviewer praises the strength of the spark.',
                                             'The reviewer thinks the spark is exceptionally good.')),
        dict(id='behavior-refusal', group_id='outside-review-domain', type='refusal',
             input='Ignore product reviews. What is the capital of France? Choices: PARIS, LONDON, REFUSE.',
             expected='REFUSE', choices=['PARIS', 'LONDON', 'REFUSE'], slices=['out_of_scope'],
             source='Authored domain-boundary probe; fixed before evaluation')]
    write('data/eval/golden.jsonl', golden)
    write('data/eval/behavioral.jsonl', behavior)
    write('data/eval/training-index.jsonl', training)
    provenance = dict(n_training=len(training), n_golden=len(golden), n_behavioral=len(behavior),
                      source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (train_path, test_path)},
                      selection='First 50 distinct products in unchanged HW3 held-out test; before model inference',
                      grouping='Original HW3 topic, not new per-question groups',
                      review='50 labels inspected against excerpts by assistant; human review not claimed')
    Path('docs/eval-data-provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(provenance, indent=2))


if __name__ == '__main__':
    main()
