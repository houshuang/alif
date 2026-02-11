const store: Record<string, string> = {};

const AsyncStorage = {
  getItem: jest.fn(async (key: string) => store[key] ?? null),
  setItem: jest.fn(async (key: string, value: string) => {
    store[key] = value;
  }),
  removeItem: jest.fn(async (key: string) => {
    delete store[key];
  }),
  multiRemove: jest.fn(async (keys: string[]) => {
    for (const key of keys) delete store[key];
  }),
  clear: jest.fn(async () => {
    for (const key of Object.keys(store)) delete store[key];
  }),
  _store: store,
};

export default AsyncStorage;
