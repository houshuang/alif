/** @type {import('jest').Config} */
module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  roots: ["<rootDir>/lib"],
  moduleFileExtensions: ["ts", "tsx", "js", "jsx"],
  testMatch: ["**/__tests__/**/*.test.ts"],
  moduleNameMapper: {
    "^@react-native-async-storage/async-storage$":
      "<rootDir>/lib/__tests__/__mocks__/async-storage.ts",
    "^expo-constants$": "<rootDir>/lib/__tests__/__mocks__/expo-constants.ts",
    "^@react-native-community/netinfo$":
      "<rootDir>/lib/__tests__/__mocks__/netinfo.ts",
  },
};
