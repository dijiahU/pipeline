require "json"

manifest = JSON.parse(File.read("/tmp/discourse_seed_manifest.json"))
admin_email = ENV.fetch("DISCOURSE_ADMIN_EMAIL")
admin_password = ENV.fetch("DISCOURSE_ADMIN_PASSWORD")

def find_user_by_email(email)
  user_email = UserEmail.find_by(email: email) || UserEmail.find_by(normalized_email: email.downcase)
  user_email&.user
end

def ensure_user(username:, email:, name:, password:, admin: false, moderator: false)
  user = User.find_by(username_lower: username.downcase) || find_user_by_email(email) || User.new
  is_new_user = user.new_record?
  user.username = username
  user.name = name
  user.email = email
  user.password = password if is_new_user
  user.active = true
  user.approved = true
  user.admin = admin
  user.moderator = moderator
  user.save!
  user
end

ensure_user(
  username: "admin",
  email: admin_email,
  name: "Pipeline Admin",
  password: admin_password,
  admin: true,
  moderator: true,
)

(manifest["users"] || []).each do |entry|
  username = entry.fetch("username")
  role = entry.fetch("role", "member")
  ensure_user(
    username: username,
    email: entry.fetch("email", "#{username}@example.com"),
    name: entry.fetch("name", username.capitalize),
    password: entry.fetch("password", "UserPassword123!"),
    admin: role == "admin",
    moderator: role == "staff",
  )
end

puts "ok"
