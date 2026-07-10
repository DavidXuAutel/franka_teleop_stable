#include <iostream>
#include <string>
#include <franka/exception.h>
#include <franka/gripper.h>
int main(int argc, char** argv) {
  if (argc != 2) { std::cerr << "Usage: " << argv[0] << " <robot-ip>\n"; return 2; }
  try {
    franka::Gripper gripper(argv[1]);
    franka::GripperState s = gripper.readOnce();
    std::cout << s << std::endl;
    return 0;
  } catch (const franka::Exception& e) {
    std::cerr << e.what() << std::endl; return 1;
  }
}
