const { subtotal } = require('./cart');

test('subtotal', () => {
  expect(subtotal([{ price: 2 }, { price: 3 }])).toBe(5);
});
